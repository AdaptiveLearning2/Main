#include "muse_bridge_service.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

namespace {
long long steady_now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

bool env_flag_off(const char* name) {
    const char* raw = std::getenv(name);
    if (!raw || !*raw) {
        return false;
    }
    const std::string value(raw);
    return value == "0" || value == "false" || value == "FALSE" || value == "no" || value == "off";
}

// Retry a dropped link without a click. On by default; MUSE_AUTO_RECONNECT=0 disables.
bool auto_reconnect_enabled() {
    static const bool enabled = !env_flag_off("MUSE_AUTO_RECONNECT");
    return enabled;
}

// CONNECTED with no EEG for this long is a dead link. Generous, since a preset switch pauses
// streaming; a judgement, not a measurement. 0 disables the watchdog only.
long long liveness_timeout_ms() {
    static const long long value = [] {
        const char* raw = std::getenv("MUSE_LIVENESS_TIMEOUT_MS");
        if (!raw || !*raw) {
            return 8000LL;
        }
        char* end = nullptr;
        const long parsed = std::strtol(raw, &end, 10);
        if (end == raw || *end != '\0' || parsed < 0) {
            std::cerr << "Invalid MUSE_LIVENESS_TIMEOUT_MS='" << raw << "'; using 8000\n";
            return 8000LL;
        }
        return static_cast<long long>(parsed);
    }();
    return value;
}

// Wait before each attempt: short first (most BLE drops are momentary), then backing off.
constexpr long long RECONNECT_BACKOFF_MS[] = {2000, 4000, 8000, 16000, 30000};
static_assert(sizeof(RECONNECT_BACKOFF_MS) / sizeof(RECONNECT_BACKOFF_MS[0])
                  >= MuseBridgeService::MAX_RECONNECT_ATTEMPTS,
              "one backoff entry per attempt");
// An attempt not CONNECTED by then has failed; connect_named() returns before the outcome is known.
constexpr long long RECONNECT_ATTEMPT_TIMEOUT_MS = 15000;
// A restored link must stay up this long to reset the attempt budget, not just reach CONNECTED.
// See docs/signals.md.
constexpr long long LINK_STABLE_MS = 30000;
}  // namespace

#if defined(ENABLE_LIBMUSE)
#include <winrt/Windows.Devices.Radios.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Foundation.h>

namespace {
void wait_for_disconnect(const std::shared_ptr<interaxon::bridge::Muse>& muse, int timeout_ms = 3000) {
    if (!muse) {
        return;
    }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (std::chrono::steady_clock::now() < deadline) {
        if (muse->get_connection_state() == interaxon::bridge::ConnectionState::DISCONNECTED) {
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
}
} // namespace

class MuseBridgeService::BridgeMuseListener final : public interaxon::bridge::MuseListener {
public:
    explicit BridgeMuseListener(MuseBridgeService& service) : service_(service) {}
    void muse_list_changed() override { service_.rebuild_muse_name_list(); }

private:
    MuseBridgeService& service_;
};

class MuseBridgeService::BridgeDataListener final : public interaxon::bridge::MuseDataListener {
public:
    explicit BridgeDataListener(MuseBridgeService& service) : service_(service) {}
    void receive_muse_data_packet(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet,
                                  const std::shared_ptr<interaxon::bridge::Muse>&) override {
        switch (packet->packet_type()) {
        case interaxon::bridge::MuseDataPacketType::NOTCH_FILTERED_EEG:
            // Raw EEG with 45-65Hz mains hum removed; preferred, since hum grows as contact worsens.
            service_.note_notch_available();
            service_.last_any_eeg_ms_.store(steady_now_ms());
            service_.enqueue_frame(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::EEG:
            // Stamped even when not enqueued: the watchdog asks whether the link delivers at all.
            service_.last_any_eeg_ms_.store(steady_now_ms());
            // Raw only while notch-filtered packets aren't arriving.
            if (!service_.notch_available()) {
                service_.enqueue_frame(packet);
            }
            break;
        case interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE:
            service_.update_band_power(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::HSI_PRECISION:
        case interaxon::bridge::MuseDataPacketType::IS_GOOD:
            service_.update_contact_quality(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::OPTICS:
        case interaxon::bridge::MuseDataPacketType::PPG:
            // 2025 models carry PPG inside OPTICS; 2018-2024 models send PPG only.
            service_.update_optical(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::IS_PPG_GOOD:
        case interaxon::bridge::MuseDataPacketType::IS_HEART_GOOD:
            service_.update_optical_quality(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::BATTERY:
            service_.update_battery(packet);
            break;
        default:
            break;
        }
    }
    void receive_muse_artifact_packet(const interaxon::bridge::MuseArtifactPacket&,
                                      const std::shared_ptr<interaxon::bridge::Muse>&) override {}

private:
    MuseBridgeService& service_;
};

class MuseBridgeService::BridgeConnectionListener final : public interaxon::bridge::MuseConnectionListener {
public:
    explicit BridgeConnectionListener(MuseBridgeService& service) : service_(service) {}
    void receive_muse_connection_packet(const interaxon::bridge::MuseConnectionPacket& packet,
                                        const std::shared_ptr<interaxon::bridge::Muse>& muse) override {
        service_.update_connection_state(packet.current_connection_state);
        // get_model() returns MU_02 for any 2018+ headband until CONNECTED.
        if (packet.current_connection_state == interaxon::bridge::ConnectionState::CONNECTED) {
            service_.apply_model_preset(muse);
        }
    }

private:
    MuseBridgeService& service_;
};
#endif

MuseBridgeService::MuseBridgeService() : running_(false), frame_counter_(0) {}

MuseBridgeService::~MuseBridgeService() {
    stop();
}

bool MuseBridgeService::start() {
    running_.store(true);
    frame_counter_ = 0;

#if defined(ENABLE_LIBMUSE)
    manager_ = interaxon::bridge::MuseManagerWindows::get_instance();
    if (!manager_) {
        return false;
    }

    muse_listener_ = std::make_shared<BridgeMuseListener>(*this);
    data_listener_ = std::make_shared<BridgeDataListener>(*this);
    connection_listener_ = std::make_shared<BridgeConnectionListener>(*this);

    manager_->set_muse_listener(muse_listener_);
    manager_->remove_from_list_after(0);
    refresh_bluetooth_state();
    manager_->start_listening();
#endif

    return true;
}

void MuseBridgeService::stop() {
    running_.store(false);
    // Joins the reconnect thread before manager_ and active_muse_ are freed.
    cancel_auto_reconnect();

#if defined(ENABLE_LIBMUSE)
    {
        std::shared_ptr<interaxon::bridge::Muse> muse;
        {
            std::lock_guard<std::mutex> lock(queue_mutex_);
            muse = std::move(active_muse_);
        }
        if (muse) {
            muse->disconnect();
            wait_for_disconnect(muse);
            unregister_data_listeners(muse);
            muse->unregister_connection_listener(connection_listener_);
        }
    }
    if (manager_) {
        manager_->stop_listening();
        manager_.reset();
    }
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        muse_names_.clear();
        discovered_ = false;
        reset_device_fields_locked();
        band_channels_used_ = 0;
        last_notch_ms_.store(0);
        connected_ = false;
        last_connection_state_ = static_cast<int>(interaxon::bridge::ConnectionState::UNKNOWN);
    }
#endif
}

void MuseBridgeService::refresh_scan() {
#if defined(ENABLE_LIBMUSE)
    if (!manager_) {
        return;
    }
    // Re-checked every refresh, so a radio toggled off mid-session is caught.
    refresh_bluetooth_state();
    manager_->stop_listening();
    manager_->start_listening();
#endif
}

#if defined(ENABLE_LIBMUSE)
namespace {

// Move a capable headband onto an optics preset. Off by default: leaving PRESET_21
// changes EEG bit depth (12 -> 14) and sometimes channel count.
bool optics_preset_enabled() {
    const char* raw = std::getenv("MUSE_ENABLE_OPTICS");
    if (!raw || !*raw) {
        return false;
    }
    const std::string value(raw);
    return value == "1" || value == "true" || value == "TRUE" || value == "yes";
}

// Names only the presets this bridge asks for; the rest are numeric.
std::string preset_name(interaxon::bridge::MusePreset preset) {
    switch (preset) {
    case interaxon::bridge::MusePreset::PRESET_21: return "PRESET_21";
    case interaxon::bridge::MusePreset::PRESET_1031: return "PRESET_1031";
    case interaxon::bridge::MusePreset::PRESET_1032: return "PRESET_1032";
    case interaxon::bridge::MusePreset::PRESET_1033: return "PRESET_1033";
    case interaxon::bridge::MusePreset::PRESET_1034: return "PRESET_1034";
    case interaxon::bridge::MusePreset::PRESET_1035: return "PRESET_1035";
    case interaxon::bridge::MusePreset::PRESET_1036: return "PRESET_1036";
    default: return "PRESET_" + std::to_string(static_cast<int>(preset));
    }
}

// Which optics preset to ask an Athena for; configurable because of the BLE bandwidth
// cliff (docs/signals.md). Below 16 CH loses Red/Ambient: fine for HR (850nm IR), not SpO2.
struct OpticsPresetChoice {
    interaxon::bridge::MusePreset preset;
    const char* label;
};

OpticsPresetChoice optics_preset_choice() {
    const char* raw = std::getenv("MUSE_OPTICS_PRESET");
    const std::string value = (raw && *raw) ? std::string(raw) : std::string("1035");
    if (value == "1031") {
        return {interaxon::bridge::MusePreset::PRESET_1031, "PRESET_1031"};  // 16 CH, low power
    }
    if (value == "1032") {
        return {interaxon::bridge::MusePreset::PRESET_1032, "PRESET_1032"};  // 16 CH, high power
    }
    if (value == "1033") {
        return {interaxon::bridge::MusePreset::PRESET_1033, "PRESET_1033"};  // 8 CH, low power
    }
    if (value == "1034") {
        return {interaxon::bridge::MusePreset::PRESET_1034, "PRESET_1034"};  // 8 CH, high power
    }
    if (value == "1036") {
        return {interaxon::bridge::MusePreset::PRESET_1036, "PRESET_1036"};  // 4 CH, high power
    }
    // An unrecognised value is a typo: warn once, not every reconnect.
    if (raw && *raw && value != "1035") {
        // atomic: runs on the connection listener thread, which reconnects can race.
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true)) {
            std::cerr << "Unrecognised MUSE_OPTICS_PRESET='" << value
                      << "' (expected 1031-1036); using PRESET_1035\n";
        }
    }
    // Default: 4 CH, low power -- least bandwidth that still carries a pulse.
    return {interaxon::bridge::MusePreset::PRESET_1035, "PRESET_1035"};
}

// Enum names differ from the hardware label: MU_04/MU_05 are the 2019/2021 Muse S (MS-01/MS-02).
const char* model_name(interaxon::bridge::MuseModel model) {
    switch (model) {
    case interaxon::bridge::MuseModel::MU_01: return "MU-01";
    case interaxon::bridge::MuseModel::MU_02: return "MU-02";
    case interaxon::bridge::MuseModel::MU_03: return "MU-03";
    case interaxon::bridge::MuseModel::MU_04: return "MS-01";
    case interaxon::bridge::MuseModel::MU_05: return "MS-02";
    case interaxon::bridge::MuseModel::MU_06: return "MU-06";
    case interaxon::bridge::MuseModel::MS_03: return "MS-03";
    default: return "";
    }
}

// Capability, not observation: MU-01/MU-02 have no PPG; the 2018 Muse 2 onwards do.
bool model_has_optical(interaxon::bridge::MuseModel model) {
    switch (model) {
    case interaxon::bridge::MuseModel::MU_01:
    case interaxon::bridge::MuseModel::MU_02:
        return false;
    default:
        return true;
    }
}

} // namespace
#endif

#if defined(ENABLE_LIBMUSE)
void MuseBridgeService::apply_model_preset(const std::shared_ptr<interaxon::bridge::Muse>& muse) {
    if (!muse) {
        return;
    }
    const interaxon::bridge::MuseModel model = muse->get_model();
    const bool has_optical = model_has_optical(model);

    // PRESET_21 unless optics are asked for; MS_03 is the only model with a PRESET_10xx range.
    const char* requested = "PRESET_21";
    if (optics_preset_enabled() && model == interaxon::bridge::MuseModel::MS_03) {
        // Interrupts then restores streaming; active_preset() reads back whether it took.
        const OpticsPresetChoice choice = optics_preset_choice();
        muse->set_preset(choice.preset);
        requested = choice.label;
    }

    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        muse_model_ = model_name(model);
        requested_preset_ = requested;
        optical_supported_ = has_optical;
    }
}

void MuseBridgeService::reset_device_fields_locked() {
    // Runs on every headband swap, so seq restarts and no stale samples carry over.
    while (!eeg_queue_.empty()) {
        eeg_queue_.pop();
    }
    while (!optics_queue_.empty()) {
        optics_queue_.pop();
    }
    optics_seq_ = 0;

    active_muse_name_.clear();
    firmware_version_.clear();
    // Everything describing the departed headband goes, so nothing stale reads as current.
    muse_model_.clear();
    requested_preset_.clear();
    optical_supported_ = false;
    battery_percent_ = -1.0;
    latest_bands_ = BandPowers{};
    latest_contact_ = ContactQuality{};
    latest_optical_ = OpticalSignals{};
}
#endif

bool MuseBridgeService::connect_named(const std::string& name) {
#if defined(ENABLE_LIBMUSE)
    if (name.empty() || !manager_) {
        return false;
    }
    disconnect_muse();

    std::shared_ptr<interaxon::bridge::Muse> chosen;
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        for (const auto& m : manager_->get_muses()) {
            if (m->get_name() == name) {
                chosen = m;
                break;
            }
        }
    }
    if (!chosen) {
        return false;
    }

    manager_->stop_listening();
    chosen->register_connection_listener(connection_listener_);
    // Raw 4-channel EEG at 220Hz (PRESET_21). All registered before run_asynchronously().
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::EEG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::NOTCH_FILTERED_EEG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::HSI_PRECISION);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_GOOD);
    // Optical, unconditionally, so "no optics" differs from "didn't ask".
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::OPTICS);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::PPG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_PPG_GOOD);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_HEART_GOOD);
    // Battery is preset-independent telemetry.
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::BATTERY);
    chosen->set_preset(interaxon::bridge::MusePreset::PRESET_21);
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        active_muse_ = chosen;
        active_muse_name_ = chosen->get_name();
        last_connected_name_ = active_muse_name_;
    }
    chosen->run_asynchronously();
    return true;
#else
    (void)name;
    return false;
#endif
}

void MuseBridgeService::disconnect_muse() {
#if defined(ENABLE_LIBMUSE)
    std::shared_ptr<interaxon::bridge::Muse> muse;
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        muse = std::move(active_muse_);
        reset_device_fields_locked();
        band_channels_used_ = 0;
        last_notch_ms_.store(0);
        connected_ = false;
        last_connection_state_ = static_cast<int>(interaxon::bridge::ConnectionState::DISCONNECTED);
    }
    if (muse) {
        muse->disconnect();
        wait_for_disconnect(muse);
        unregister_data_listeners(muse);
        muse->unregister_connection_listener(connection_listener_);
    }
#endif
}

std::vector<std::string> MuseBridgeService::muse_names() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return muse_names_;
#else
    return {};
#endif
}

std::string MuseBridgeService::active_muse_name() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return active_muse_name_;
#else
    return {};
#endif
}

std::string MuseBridgeService::firmware_version() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return firmware_version_;
#else
    return {};
#endif
}

// Body guarded: queue_mutex_ exists only under ENABLE_LIBMUSE, and CI compiles OFF.
std::string MuseBridgeService::muse_model() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return muse_model_;
#else
    return {};
#endif
}

// Outside the guard: main.cpp calls it unconditionally, and CI compiles OFF.
OpticalSignals MuseBridgeService::optical_signals() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return latest_optical_;
#else
    return {};
#endif
}

long long MuseBridgeService::optical_age_ms() const {
#if defined(ENABLE_LIBMUSE)
    long long last = 0;
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        last = latest_optical_.last_ms;
    }
    if (last == 0) {
        return -1;
    }
    const long long now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
    return now_ms - last;
#else
    return -1;
#endif
}

std::string MuseBridgeService::requested_preset() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return requested_preset_;
#else
    return {};
#endif
}

DeviceConfig MuseBridgeService::device_config() const {
#if defined(ENABLE_LIBMUSE)
    std::shared_ptr<interaxon::bridge::Muse> muse;
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        muse = active_muse_;
    }
    if (!muse) {
        return {};
    }
    // Read live, outside the lock: holding queue_mutex_ into libMuse would block the data listener.
    const auto config = muse->get_muse_configuration();
    if (!config) {
        return {};
    }
    DeviceConfig out;
    out.preset = preset_name(config->get_preset());
    out.eeg_channel_count = config->get_eeg_channel_count();
    out.known = true;
    return out;
#else
    return {};
#endif
}

bool MuseBridgeService::optical_supported() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return optical_supported_;
#else
    return false;
#endif
}

double MuseBridgeService::battery_percent() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return battery_percent_;
#else
    return -1.0;
#endif
}

BandPowers MuseBridgeService::band_powers() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return latest_bands_;
#else
    return BandPowers{};
#endif
}

ContactQuality MuseBridgeService::contact_quality() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return latest_contact_;
#else
    return ContactQuality{};
#endif
}

bool MuseBridgeService::notch_available() const {
    const long long last = last_notch_ms_.load();
    if (last == 0) {
        return false;
    }
    return (steady_now_ms() - last) <= NOTCH_STALE_MS;
}

void MuseBridgeService::note_notch_available() {
    last_notch_ms_.store(steady_now_ms());
}

int MuseBridgeService::band_channels_used() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return band_channels_used_;
#else
    return 0;
#endif
}

bool MuseBridgeService::poll_optics(OpticsFrame& frame) {
    if (!running_.load()) {
        return false;
    }
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    if (optics_queue_.empty()) {
        return false;
    }
    frame = optics_queue_.front();
    optics_queue_.pop();
    return true;
#else
    (void)frame;
    return false;
#endif
}

bool MuseBridgeService::poll_frame(EegFrame& frame) {
    if (!running_.load()) {
        return false;
    }

#if defined(ENABLE_LIBMUSE)
    {
        std::unique_lock<std::mutex> lock(queue_mutex_);
        if (eeg_queue_.empty()) {
            queue_cv_.wait_for(lock, std::chrono::milliseconds(200));
        }
        if (!eeg_queue_.empty()) {
            frame = eeg_queue_.front();
            eeg_queue_.pop();
            return true;
        }
    }
    return false;
#endif

    // Synthetic stream keeps the pipeline testable without hardware.
    std::this_thread::sleep_for(std::chrono::milliseconds(4)); // Approx 250 Hz
    const double t = static_cast<double>(frame_counter_) / 250.0;
    frame_counter_ += 1;

    frame.mono_ts_ms =
        static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count());
    // Matches SignalProcessor calibration (raw Muse-like range ~500-850).
    frame.tp9 = 700.0 + std::sin(t * 3.14 * 2.0) * 35.0;
    frame.af7 = 704.0 + std::sin(t * 3.14 * 2.0 + 0.3) * 35.0;
    frame.af8 = 698.0 + std::sin(t * 3.14 * 2.0 + 0.6) * 35.0;
    frame.tp10 = 706.0 + std::sin(t * 3.14 * 2.0 + 0.9) * 35.0;
    latest_bands_.delta = 0.55 + std::sin(t * 1.5) * 0.05;
    latest_bands_.theta = 0.48 + std::sin(t * 1.8 + 0.2) * 0.05;
    latest_bands_.alpha = 0.62 + std::sin(t * 2.2 + 0.4) * 0.05;
    latest_bands_.beta = 0.44 + std::sin(t * 2.6 + 0.6) * 0.05;
    latest_bands_.gamma = 0.33 + std::sin(t * 3.1 + 0.8) * 0.05;
    return true;
}

#if defined(ENABLE_LIBMUSE)
void MuseBridgeService::enqueue_frame(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    EegFrame frame{};
    frame.mono_ts_ms = packet->timestamp() / 1000;
    frame.tp9 = packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG1);
    frame.af7 = packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG2);
    frame.af8 = packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG3);
    frame.tp10 = packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG4);

    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        eeg_queue_.push(frame);
        if (eeg_queue_.size() > 2048) {
            eeg_queue_.pop();
        }
    }
    queue_cv_.notify_all();
}

void MuseBridgeService::unregister_data_listeners(
    const std::shared_ptr<interaxon::bridge::Muse>& muse) {
    // Must match the registrations in connect_named().
    static constexpr interaxon::bridge::MuseDataPacketType kTypes[] = {
        interaxon::bridge::MuseDataPacketType::EEG,
        interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE,
        interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE,
        interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE,
        interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE,
        interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE,
        interaxon::bridge::MuseDataPacketType::NOTCH_FILTERED_EEG,
        interaxon::bridge::MuseDataPacketType::HSI_PRECISION,
        interaxon::bridge::MuseDataPacketType::IS_GOOD,
        interaxon::bridge::MuseDataPacketType::OPTICS,
        interaxon::bridge::MuseDataPacketType::PPG,
        interaxon::bridge::MuseDataPacketType::IS_PPG_GOOD,
        interaxon::bridge::MuseDataPacketType::IS_HEART_GOOD,
        interaxon::bridge::MuseDataPacketType::BATTERY,
    };
    for (const auto type : kTypes) {
        muse->unregister_data_listener(data_listener_, type);
    }
}

void MuseBridgeService::update_optical(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    // values(), not named accessors: absent channels on a narrower preset return garbage.
    const std::vector<double> values = packet->values();
    const bool is_optics = packet->packet_type() == interaxon::bridge::MuseDataPacketType::OPTICS;
    const long long now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();

    std::lock_guard<std::mutex> lock(queue_mutex_);
    // Count clamped to what's stored, so count and array agree.
    if (is_optics) {
        latest_optical_.optics_packets += 1;
        latest_optical_.last_optics.fill(0.0);
        const size_t n = std::min(values.size(), latest_optical_.last_optics.size());
        for (size_t i = 0; i < n; ++i) {
            latest_optical_.last_optics[i] = values[i];
        }
        latest_optical_.optics_values = static_cast<int>(n);
    } else {
        latest_optical_.ppg_packets += 1;
        latest_optical_.last_ppg.fill(0.0);
        const size_t n = std::min(values.size(), latest_optical_.last_ppg.size());
        for (size_t i = 0; i < n; ++i) {
            latest_optical_.last_ppg[i] = values[i];
        }
        latest_optical_.ppg_values = static_cast<int>(n);
    }
    latest_optical_.last_ms = now_ms;

    // Queue OPTICS only: PPG has a different channel mapping (2018-2024 hardware).
    if (is_optics) {
        OpticsFrame frame{};
        // libMuse reports microseconds.
        frame.mono_ts_ms = packet->timestamp() / 1000;
        frame.seq = ++optics_seq_;
        const size_t n = std::min(values.size(), frame.ch.size());
        for (size_t i = 0; i < n; ++i) {
            frame.ch[i] = values[i];
        }
        frame.n = static_cast<int>(n);
        optics_queue_.push(frame);
        // ~32s at 64Hz. Drops are counted: the time base is rebuilt from sample index.
        if (optics_queue_.size() > 2048) {
            optics_queue_.pop();
            latest_optical_.optics_dropped += 1;
        }
    }
}

void MuseBridgeService::update_battery(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    const double pct = packet->get_battery_value(
        interaxon::bridge::Battery::CHARGE_PERCENTAGE_REMAINING);
    // Out of range is dropped, not clamped: -1 means "not reported", and the last good reading wins.
    if (!(pct >= 0.0 && pct <= 100.0)) {
        return;
    }
    std::lock_guard<std::mutex> lock(queue_mutex_);
    battery_percent_ = pct;
}

void MuseBridgeService::update_optical_quality(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    const std::vector<double> values = packet->values();
    if (values.empty()) {
        return;
    }
    // libMuse's own verdict; non-zero is good, as for IS_GOOD.
    const bool good = values[0] != 0.0;
    const bool is_ppg = packet->packet_type() == interaxon::bridge::MuseDataPacketType::IS_PPG_GOOD;

    std::lock_guard<std::mutex> lock(queue_mutex_);
    if (is_ppg) {
        latest_optical_.ppg_good = good;
        latest_optical_.has_ppg_good = true;
    } else {
        latest_optical_.heart_good = good;
        latest_optical_.has_heart_good = true;
    }
}

void MuseBridgeService::update_band_power(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    const std::array<double, 4> per_channel{{
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG1),
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG2),
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG3),
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG4),
    }};

    std::lock_guard<std::mutex> lock(queue_mutex_);

    // Average only electrodes the headband calls usable; all four when none are, or no contact data yet.
    double sum = 0.0;
    int used = 0;
    if (latest_contact_.has_is_good || latest_contact_.has_hsi) {
        for (size_t i = 0; i < per_channel.size(); ++i) {
            const bool valid_data = !latest_contact_.has_is_good || latest_contact_.is_good[i] >= 1.0;
            // <= 2.0 also admits 0 (not reported), which isn't evidence of a bad fit.
            const double fit = latest_contact_.hsi[i];
            const bool seated = !latest_contact_.has_hsi || fit <= 2.0;
            if (valid_data && seated) {
                sum += per_channel[i];
                ++used;
            }
        }
    }
    if (used == 0) {
        sum = per_channel[0] + per_channel[1] + per_channel[2] + per_channel[3];
        used = 4;
    }
    const double avg = sum / static_cast<double>(used);
    band_channels_used_ = used;
    switch (packet->packet_type()) {
    case interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE:
        latest_bands_.delta = avg;
        break;
    case interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE:
        latest_bands_.theta = avg;
        break;
    case interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE:
        latest_bands_.alpha = avg;
        break;
    case interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE:
        latest_bands_.beta = avg;
        break;
    case interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE:
        latest_bands_.gamma = avg;
        break;
    default:
        break;
    }
}

void MuseBridgeService::update_contact_quality(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    // HSI_PRECISION and IS_GOOD use the EEG packet's channel mapping.
    const std::array<double, 4> values{{
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG1),
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG2),
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG3),
        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG4),
    }};

    std::lock_guard<std::mutex> lock(queue_mutex_);
    if (packet->packet_type() == interaxon::bridge::MuseDataPacketType::HSI_PRECISION) {
        latest_contact_.hsi = values;
        latest_contact_.has_hsi = true;
    } else {
        latest_contact_.is_good = values;
        latest_contact_.has_is_good = true;
    }
}

void MuseBridgeService::rebuild_muse_name_list() {
    std::lock_guard<std::mutex> lock(queue_mutex_);
    muse_names_.clear();
    if (!manager_) {
        discovered_ = false;
        return;
    }
    for (const auto& m : manager_->get_muses()) {
        muse_names_.push_back(m->get_name());
    }
    discovered_ = !muse_names_.empty();
}

void MuseBridgeService::update_connection_state(interaxon::bridge::ConnectionState state) {
    // Never call libMuse here: it holds an internal lock during callbacks, so re-entry deadlocks.
    // No queue/seq reset: this fires on transient blips too; connect_named() resets.
    std::lock_guard<std::mutex> lock(queue_mutex_);
    const bool was_connected = connected_;
    last_connection_state_ = static_cast<int>(state);
    connected_ = (state == interaxon::bridge::ConnectionState::CONNECTED);
    if (connected_) {
        // Fresh watchdog baseline; CONNECTED is the only sign an attempt succeeded.
        connected_since_ms_.store(steady_now_ms());
        last_any_eeg_ms_.store(0);
        if (reconnect_armed_.load() || reconnect_in_flight_.load()) {
            std::cerr << "auto-reconnect: link restored on attempt "
                      << reconnect_attempt_.load() << "\n";
        }
        // Attempt count not reset: that waits for LINK_STABLE_MS or a person's command.
        reconnect_armed_.store(false);
        reconnect_in_flight_.store(false);
        return;
    }
    connected_since_ms_.store(0);
    last_any_eeg_ms_.store(0);
    firmware_version_.clear();
    battery_percent_ = -1.0;
    // Only a CONNECTED -> not-CONNECTED edge arms recovery. disconnect_muse() clears connected_
    // before the SDK call, so a deliberate disconnect arms nothing; that ordering is the guard.
    if (was_connected && auto_reconnect_enabled()) {
        std::cerr << "auto-reconnect: link dropped (state " << static_cast<int>(state)
                  << "), will retry\n";
        arm_reconnect();
    }
}
#endif

// Outside the ENABLE_LIBMUSE guard: main.cpp calls these unconditionally, and CI compiles OFF.
void MuseBridgeService::arm_reconnect() {
    // Flag-only: called inside the connection callback, where re-entering libMuse deadlocks.
    // The attempt count carries over; only a stable link or a person's command resets it.
    reconnect_in_flight_.store(false);
    schedule_next_reconnect();
}

void MuseBridgeService::schedule_next_reconnect() {
    const int made = reconnect_attempt_.load();
    reconnect_in_flight_.store(false);
    if (made >= MAX_RECONNECT_ATTEMPTS) {
        reconnect_armed_.store(false);
        reconnect_exhausted_.store(true);
        std::cerr << "auto-reconnect: giving up after " << made
                  << " attempts; click Connect to try again\n";
        return;
    }
    next_reconnect_at_ms_.store(steady_now_ms() + RECONNECT_BACKOFF_MS[made]);
    reconnect_armed_.store(true);
}

void MuseBridgeService::launch_reconnect_attempt() {
    std::string name;
#if defined(ENABLE_LIBMUSE)
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        name = last_connected_name_;
    }
#endif
    if (name.empty()) {
        reconnect_armed_.store(false);
        return;
    }
    // Only reached once the previous thread signalled done, so this join never blocks.
    if (reconnect_thread_.joinable()) {
        reconnect_thread_.join();
    }
    const int attempt = reconnect_attempt_.load() + 1;
    reconnect_attempt_.store(attempt);
    reconnect_armed_.store(false);
    reconnect_in_flight_.store(true);
    attempt_started_ms_.store(steady_now_ms());
    reconnect_thread_done_.store(false);
    const int generation = reconnect_generation_.load();
    std::cerr << "auto-reconnect: attempt " << attempt << "/" << MAX_RECONNECT_ATTEMPTS
              << " to " << name << "\n";
    // Own thread: connect_named() can block up to 3s in wait_for_disconnect.
    reconnect_thread_ = std::thread([this, name, generation] {
        const bool launched = connect_named(name);
        if (reconnect_generation_.load() != generation) {
            // A person took over meanwhile; undo this connect.
            if (launched) {
                disconnect_muse();
            }
        } else if (!launched) {
            // Gone from the scan list (usually power-cycled, new advertisement): rescan.
            refresh_scan();
            schedule_next_reconnect();
        }
        // Launched attempts are judged by the connection callback or the attempt timeout.
        reconnect_thread_done_.store(true);
    });
}

void MuseBridgeService::service_auto_reconnect() {
    if (!running_.load() || !auto_reconnect_enabled()) {
        return;
    }
    const long long now = steady_now_ms();

    // ── a restored link that has held ──
    if (is_muse_connected() && reconnect_attempt_.load() > 0) {
        const long long since = connected_since_ms_.load();
        if (since > 0 && now - since > LINK_STABLE_MS) {
            std::cerr << "auto-reconnect: link stable for " << (now - since)
                      << "ms; attempt budget reset\n";
            reconnect_attempt_.store(0);
            reconnect_exhausted_.store(false);
        }
    }

    // ── watchdog ──
    // From the later of last packet and connect, so a preset switch's pause isn't a dead link.
    const long long liveness = liveness_timeout_ms();
    if (liveness > 0 && is_muse_connected() && !reconnect_in_flight_.load()) {
        const long long since = std::max(last_any_eeg_ms_.load(), connected_since_ms_.load());
        if (since > 0 && now - since > liveness) {
            std::cerr << "auto-reconnect: no EEG for " << (now - since)
                      << "ms while CONNECTED; treating the link as dropped\n";
            // disconnect_muse() resets state, but its callback arms nothing, hence the explicit arm.
            disconnect_muse();
            arm_reconnect();
            return;
        }
    }

    // ── an attempt that never reached CONNECTED ──
    if (reconnect_in_flight_.load() && reconnect_thread_done_.load()
            && now - attempt_started_ms_.load() > RECONNECT_ATTEMPT_TIMEOUT_MS) {
        std::cerr << "auto-reconnect: attempt " << reconnect_attempt_.load()
                  << " did not reach CONNECTED within " << RECONNECT_ATTEMPT_TIMEOUT_MS << "ms\n";
        schedule_next_reconnect();
        return;
    }

    // ── the next attempt is due ──
    if (reconnect_armed_.load() && !reconnect_in_flight_.load()
            && reconnect_thread_done_.load() && now >= next_reconnect_at_ms_.load()) {
        launch_reconnect_attempt();
    }
}

void MuseBridgeService::cancel_auto_reconnect() {
    reconnect_generation_.fetch_add(1);
    reconnect_armed_.store(false);
    reconnect_in_flight_.store(false);
    reconnect_exhausted_.store(false);
    reconnect_attempt_.store(0);
    // Join, or the attempt's compensating disconnect_muse() could drop the link the person's
    // next command makes. Blocks at most one connect_named() (~3s), only mid-attempt.
    if (reconnect_thread_.joinable() && reconnect_thread_.get_id() != std::this_thread::get_id()) {
        reconnect_thread_.join();
    }
}

ReconnectStatus MuseBridgeService::reconnect_status() const {
    ReconnectStatus out;
    out.enabled = auto_reconnect_enabled();
    out.reconnecting = reconnect_armed_.load() || reconnect_in_flight_.load();
    out.attempt = reconnect_attempt_.load();
    out.max_attempts = MAX_RECONNECT_ATTEMPTS;
    out.exhausted = reconnect_exhausted_.load();
    const long long last = last_any_eeg_ms_.load();
    if (last > 0 && is_muse_connected()) {
        out.eeg_age_ms = steady_now_ms() - last;
    }
    return out;
}

#if defined(ENABLE_LIBMUSE)
void MuseBridgeService::refresh_bluetooth_state() {
    // Blocking .get() is safe: MTA, no message pump.
    try {
        using namespace winrt::Windows::Devices::Radios;
        for (const auto& radio : Radio::GetRadiosAsync().get()) {
            if (radio.Kind() == RadioKind::Bluetooth) {
                bluetooth_enabled_.store(radio.State() == RadioState::On);
                return;
            }
        }
    } catch (const winrt::hresult_error&) {
        // Radio API unavailable: keep the last known value.
        return;
    }
    // No radio found: report enabled so this never masks the real cause.
    bluetooth_enabled_.store(true);
}
#endif

const char* MuseBridgeService::bridge_mode() const noexcept {
#if defined(ENABLE_LIBMUSE)
    return "libmuse";
#else
    return "synthetic";
#endif
}

bool MuseBridgeService::is_muse_connected() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return connected_;
#else
    return false;
#endif
}

bool MuseBridgeService::is_muse_discovered() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return discovered_;
#else
    return false;
#endif
}

bool MuseBridgeService::bluetooth_enabled() const {
#if defined(ENABLE_LIBMUSE)
    return bluetooth_enabled_.load();
#else
    return true;
#endif
}

int MuseBridgeService::connection_state() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return last_connection_state_;
#else
    return -1;
#endif
}
