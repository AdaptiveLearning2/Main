#include "muse_bridge_service.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

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
            // Same channels as raw EEG with 45-65Hz mains hum removed. Prefer
            // this when it flows, since hum gets worse as contact worsens.
            service_.note_notch_available();
            service_.enqueue_frame(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::EEG:
            // Fall back to raw only while notch-filtered packets aren't
            // arriving, so a preset without them (or one where they stop
            // mid-session) still produces data instead of going silent.
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
            // Which one arrives depends on the headband, not this build:
            // 2025 models carry PPG inside OPTICS and emit no PPG packet,
            // 2018-2024 models do the reverse. Registering only one would
            // miss half the hardware.
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
        // The model is only trustworthy once CONNECTED: get_model() returns
        // MU_02 for anything from 2018 onwards until then, and connect_named()
        // sets the preset earlier -- so asking sooner would misread an Athena
        // as a 2016 Muse.
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
    // Re-check on every refresh, not just at startup, so a radio toggled off
    // mid-session is caught on the next scan instead of relying on a stale flag.
    refresh_bluetooth_state();
    manager_->stop_listening();
    manager_->start_listening();
#endif
}

#if defined(ENABLE_LIBMUSE)
namespace {

// Whether to move a capable headband onto an Optics-carrying preset.
//
// Off by default. Switching off PRESET_21 changes EEG bit depth (12 -> 14)
// and sometimes channel count, and a silent EEG regression would be blamed
// on whatever shipped alongside it. Gated behind a flag so it can be turned
// on for one session and off again.
bool optics_preset_enabled() {
    const char* raw = std::getenv("MUSE_ENABLE_OPTICS");
    if (!raw || !*raw) {
        return false;
    }
    const std::string value(raw);
    return value == "1" || value == "true" || value == "TRUE" || value == "yes";
}

// Only presets this bridge actually asks for get names; anything else is
// reported numerically. The enum has ~90 members, too many to hand-maintain.
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

// Which optics preset to ask an Athena for.
//
// Configurable because it's a bandwidth trade measured on hardware, not
// reasoned about: 4 CH EEG at 256Hz plus 16 CH optics at 64Hz broke the BLE
// link within ~20s and collapsed electrode contact from [1,1,1,1] to
// [4,4,4,4], while PRESET_21 held for minutes with good contact. Being able
// to try fewer optical channels without a rebuild matters.
//
// Dropping the 16-channel mode loses the Red and Ambient channels. That's
// fine here: SpO2 needs red for ratio-of-ratios, but heart rate only needs
// the pulsatile component, and 850nm IR covers that.
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
    // A value that was set but not recognised is a typo, not a preference, and
    // silently running the default would present as "my setting had no effect"
    // with nothing to go on. Says so once rather than every reconnect.
    if (raw && *raw && value != "1035") {
        // atomic, not a plain bool: this runs on the connection listener
        // thread, so concurrent reconnects can race it.
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true)) {
            std::cerr << "Unrecognised MUSE_OPTICS_PRESET='" << value
                      << "' (expected 1031-1036); using PRESET_1035\n";
        }
    }
    // Default: fewest optical channels, lowest power -- least bandwidth that
    // still carries a pulse, most likely to coexist with EEG.
    return {interaxon::bridge::MusePreset::PRESET_1035, "PRESET_1035"};
}

// The enum names don't match what's printed on the hardware: MU_04/MU_05 are
// the 2019/2021 Muse S, printed MS-01/MS-02, while MS_03 is the 2025 Muse S.
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

// A capability, not a runtime observation. MU-01/MU-02 have no PPG at all;
// everything from the 2018 Muse 2 onwards does. Lets a heart channel report
// "no sensor" instead of "sensor stopped working" -- different situations.
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

    // PRESET_21 unless we're deliberately asking for optics. MS_03 is the
    // only model with a PRESET_10xx range, so it's the only one worth
    // switching. Keeps EEG at 4 channels, matching what EegFrame emits.
    const char* requested = "PRESET_21";
    if (optics_preset_enabled() && model == interaxon::bridge::MuseModel::MS_03) {
        // libMuse allows changing preset after connection; it interrupts and
        // then restores streaming. Whether the headband actually honours the
        // request is separate, which is why active_preset() reads it back
        // instead of trusting this call.
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
    // Reset here, in disconnect_muse(), not stop() -- this runs on every
    // headband swap. Without it, samples numbered contiguously across a gap
    // with no headband attached, hiding exactly what the seq detector exists
    // to catch, and the queues would resume a reconnect by emitting the
    // previous headband's stale samples.
    while (!eeg_queue_.empty()) {
        eeg_queue_.pop();
    }
    while (!optics_queue_.empty()) {
        optics_queue_.pop();
    }
    optics_seq_ = 0;

    active_muse_name_.clear();
    firmware_version_.clear();
    // Model, preset and capability describe the headband that just went away.
    // Leaving them set would report a device still connected, and
    // optical_supported specifically is what a heart channel checks before
    // falling back to the camera.
    muse_model_.clear();
    requested_preset_.clear();
    optical_supported_ = false;
    // Cleared for the same reason -- and it's the one number here a student
    // is asked to act on, so a stale "82%" beside Connect is worse than most.
    battery_percent_ = -1.0;
    latest_bands_ = BandPowers{};
    latest_contact_ = ContactQuality{};
    // Counters too, so old optics activity can't look like current activity.
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
    // EEG: raw 4-channel samples at 220Hz (PRESET_21). Band absolutes are
    // computed by libMuse from the raw EEG. All registered before
    // run_asynchronously().
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::EEG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE);
    // Electrode fit/validity from the headband itself -- the correct basis
    // for "signal quality", instead of inferring it from calmness.
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::NOTCH_FILTERED_EEG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::HSI_PRECISION);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_GOOD);
    // Optical, registered unconditionally: a headband that emits neither
    // simply never fires these, which is itself useful to know, versus
    // gating them and not being able to tell "no optics" from "didn't ask".
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::OPTICS);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::PPG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_PPG_GOOD);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_HEART_GOOD);
    // Battery is device telemetry libMuse fires on its own schedule, not
    // preset-dependent, so register it on every preset -- costs nothing on
    // PRESET_21 and is a reading a student can act on before a lesson.
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::BATTERY);
    chosen->set_preset(interaxon::bridge::MusePreset::PRESET_21);
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        active_muse_ = chosen;
        active_muse_name_ = chosen->get_name();
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

// Guarded like firmware_version(): queue_mutex_ only exists inside the
// ENABLE_LIBMUSE block, so an unguarded body would break the OFF build,
// which is the one CI compiles.
std::string MuseBridgeService::muse_model() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return muse_model_;
#else
    return {};
#endif
}

// Defined outside the ENABLE_LIBMUSE region, like the accessors below it:
// main.cpp calls this unconditionally, so nesting it in the guard would leave
// an unresolved symbol in the OFF build CI compiles.
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
    // One call for both fields, read live rather than cached: the
    // configuration is repopulated when settings change, so a preset applied
    // a moment after set_preset() still shows up.
    //
    // Outside the lock deliberately -- get_muse_configuration() is thread-safe
    // on its own, and calling into libMuse while holding queue_mutex_ would
    // block the data listener's own use of that lock.
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
    // The synthetic build has no battery to report; inventing one would put a
    // number on screen with nothing behind it.
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

namespace {
long long steady_now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}
}  // namespace

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

    // Temporary synthetic stream keeps the end-to-end pipeline testable.
    std::this_thread::sleep_for(std::chrono::milliseconds(4)); // Approx 250 Hz
    const double t = static_cast<double>(frame_counter_) / 250.0;
    frame_counter_ += 1;

    frame.mono_ts_ms =
        static_cast<long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count());
    // Keep synthetic fallback aligned with Python SignalProcessor calibration
    // (raw Muse-like channel range around 500-850).
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
    // One shared list for both teardown paths, so they can't drift out of
    // sync and silently leak a listener across reconnects.
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
    // values() instead of the named Optics/Ppg accessors: it reports how many
    // channels actually arrived, since named channels not present on a
    // narrower preset would just return garbage.
    const std::vector<double> values = packet->values();
    const bool is_optics = packet->packet_type() == interaxon::bridge::MuseDataPacketType::OPTICS;
    const long long now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();

    std::lock_guard<std::mutex> lock(queue_mutex_);
    // Clamped to what's actually stored, so the reported count and the array
    // can never disagree if a preset delivers more channels than fit.
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

    // Queue the sample too, not just the counters. OPTICS only -- PPG is a
    // separate channel mapping for 2018-2024 hardware, and mixing both into
    // one queue would give a series whose meaning changes with the headband.
    if (is_optics) {
        OpticsFrame frame{};
        // The packet's own timestamp, in ms. libMuse reports microseconds.
        frame.mono_ts_ms = packet->timestamp() / 1000;
        frame.seq = ++optics_seq_;
        const size_t n = std::min(values.size(), frame.ch.size());
        for (size_t i = 0; i < n; ++i) {
            frame.ch[i] = values[i];
        }
        frame.n = static_cast<int>(n);
        optics_queue_.push(frame);
        // Same bound as the EEG queue -- ~32s of backlog at 64Hz, far more
        // than the main loop should ever fall behind by. Counted, not just
        // dropped: since the time base is rebuilt from sample index, a
        // silent drop would shift it and show up as an impossible interval.
        if (optics_queue_.size() > 2048) {
            optics_queue_.pop();
            latest_optical_.optics_dropped += 1;
        }
    }
}

void MuseBridgeService::update_battery(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    // Named accessor, not values()[0]: this packet's fields are a fixed
    // mapping, unlike optics where the channel count itself is the unknown.
    const double pct = packet->get_battery_value(
        interaxon::bridge::Battery::CHARGE_PERCENTAGE_REMAINING);
    // Range-checked before storing: -1 means "not reported", so a garbage
    // negative reading must not land there. Dropped rather than clamped --
    // the previous good reading beats a fabricated 100.
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
    // libMuse's own verdict, not a threshold invented here. Non-zero is good,
    // matching the IS_GOOD convention used for the electrodes.
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

    // Average only the electrodes the headband says are usable, so one or two
    // badly-seated electrodes (commonly the ear contacts) don't contaminate
    // bands computed from the frontal ones that are reading cleanly.
    //
    // Falls back to all four when no contact data has arrived yet.
    double sum = 0.0;
    int used = 0;
    if (latest_contact_.has_is_good || latest_contact_.has_hsi) {
        for (size_t i = 0; i < per_channel.size(); ++i) {
            const bool valid_data = !latest_contact_.has_is_good || latest_contact_.is_good[i] >= 1.0;
            // HSI: 1 good, 2 mediocre, 4 poor, 0 = not reported. <= 2.0 covers
            // 0 too, since no reading yet isn't evidence of a bad fit.
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
    // Both HSI_PRECISION and IS_GOOD carry one value per EEG electrode and use
    // the same channel mapping as an EEG packet, so read them the same way.
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
    // Do NOT call any libMuse API (e.g. get_muse_version) here -- libMuse
    // holds an internal lock during callbacks, and re-entering the SDK
    // deadlocks.
    //
    // Deliberately doesn't drain the queues or reset optics_seq_: this fires
    // on every transition including transient blips, and a reset here would
    // discard good in-flight samples for a link that never really dropped.
    // Every reconnect the sidecar drives goes through connect_named(), which
    // calls disconnect_muse() first and resets properly there.
    std::lock_guard<std::mutex> lock(queue_mutex_);
    last_connection_state_ = static_cast<int>(state);
    connected_ = (state == interaxon::bridge::ConnectionState::CONNECTED);
    if (state != interaxon::bridge::ConnectionState::CONNECTED) {
        firmware_version_.clear();
        // Cleared with firmware, not left standing like the queues: a charge
        // percentage is a claim about a link that's currently down.
        battery_percent_ = -1.0;
    }
}

void MuseBridgeService::refresh_bluetooth_state() {
    // Blocking .get() is safe here: this process has no message pump to
    // stall (main() runs winrt::init_apartment() in MTA mode).
    try {
        using namespace winrt::Windows::Devices::Radios;
        for (const auto& radio : Radio::GetRadiosAsync().get()) {
            if (radio.Kind() == RadioKind::Bluetooth) {
                bluetooth_enabled_.store(radio.State() == RadioState::On);
                return;
            }
        }
    } catch (const winrt::hresult_error&) {
        // Radio API unavailable/denied -- keep the last known value rather
        // than reporting a false "Bluetooth is off".
        return;
    }
    // No Bluetooth radio found: default to enabled so this diagnostic never
    // masks the real cause of a failed scan.
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
