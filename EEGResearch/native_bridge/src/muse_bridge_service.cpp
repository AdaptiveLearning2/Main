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
            // Same channel mapping as raw EEG, with 45-65Hz removed. Mains hum
            // couples in harder as contact impedance rises, i.e. exactly when
            // the signal is already marginal, so prefer this when it flows.
            service_.note_notch_available();
            service_.enqueue_frame(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::EEG:
            // Fall back to raw only while notch-filtered packets are NOT
            // arriving (none within NOTCH_STALE_MS), so a preset that doesn't
            // emit them still produces data -- and so does a session where they
            // stop partway through, which a latch would have turned into a
            // total loss of frames until reconnect.
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
            // Both, because which one arrives is a property of the headband
            // rather than of this build: muse2025 carries PPG inside OPTICS and
            // emits no PPG packet, everything from 2018 to 2024 does the
            // reverse. Registering for one would work on half the hardware.
            service_.update_optical(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::IS_PPG_GOOD:
        case interaxon::bridge::MuseDataPacketType::IS_HEART_GOOD:
            service_.update_optical_quality(packet);
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
        // The model is only trustworthy from here on. bridge_muse.h:196 is
        // explicit that get_model() returns MU_02 for anything from 2018
        // onwards until CONNECTED is reached -- and connect_named() sets the
        // preset before that, so asking there would silently treat an Athena
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
    // Re-check on every explicit refresh (not just at startup) so a radio
    // toggled off mid-session is caught on the next scan, unlike GettingData32
    // which only ever checks once and relies on a possibly-stale cached flag.
    refresh_bluetooth_state();
    manager_->stop_listening();
    manager_->start_listening();
#endif
}

#if defined(ENABLE_LIBMUSE)
namespace {

// Whether to move a capable headband onto an Optics-carrying preset.
//
// Off by default, and deliberately so. PRESET_21 is what every session has run
// on until now, it is verified working on a 2025 Athena despite not being
// listed for that model, and switching preset changes EEG bit depth (12 -> 14)
// and, on some of the alternatives, channel count. A silent EEG regression
// would be attributed to whatever shipped alongside it rather than to this, so
// it goes behind a flag that can be turned on for one session and turned off
// again.
bool optics_preset_enabled() {
    const char* raw = std::getenv("MUSE_ENABLE_OPTICS");
    if (!raw || !*raw) {
        return false;
    }
    const std::string value(raw);
    return value == "1" || value == "true" || value == "TRUE" || value == "yes";
}

// Only the two this bridge ever asks for get names; anything else is reported
// numerically rather than guessed at. The enum has ~90 members and a
// hand-maintained table of them would be wrong within one SDK release.
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
// Configurable because the choice is a bandwidth trade that has to be measured
// on hardware rather than reasoned about: 4 CH EEG at 256Hz alongside 16 CH
// optics at 64Hz was verified to break the link within ~20 seconds AND to
// collapse electrode contact quality from [1,1,1,1] to [4,4,4,4] on a worn
// headband, while the same build on PRESET_21 held for minutes with good
// contact. Fewer optical channels is the obvious next thing to try, and being
// able to try it without a rebuild is the difference between one session and
// several.
//
// Losing the 16-channel mode costs the Red and Ambient channels, which exist
// only there (bridge_optics.h:51-66). That is survivable for this purpose: red
// is what a pulse oximeter needs to compute SpO2 by ratio-of-ratios, whereas a
// heart rate comes from the pulsatile component of any wavelength blood
// absorbs, and 850nm IR is the conventional choice for exactly that.
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
        // atomic exchange rather than a plain bool: this runs from the
        // connection listener thread, so concurrent reconnects can race it.
        // The cost of losing is only a duplicate stderr line, but the rest of
        // this file is careful about which thread touches what, and a lone
        // unsynchronised static invites the next one to be less careful.
        static std::atomic<bool> warned{false};
        if (!warned.exchange(true)) {
            std::cerr << "Unrecognised MUSE_OPTICS_PRESET='" << value
                      << "' (expected 1031-1036); using PRESET_1035\n";
        }
    }
    // Default: fewest optical channels, lowest power. The least bandwidth that
    // still carries a pulse, which is the end of the ladder most likely to
    // coexist with EEG rather than the end that carries the most data.
    return {interaxon::bridge::MusePreset::PRESET_1035, "PRESET_1035"};
}

// Verified against bridge_muse_model.h:14-32. The enumerator names and the
// markings on the hardware do not line up: MU_04 and MU_05 are the 2019 and
// 2021 Muse S, printed MS-01 and MS-02, while MS_03 is the 2025 Muse S. Reading
// the enumerator name straight out would report an Athena's predecessor as
// "MU-04", which is printed on no headband at all.
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

// Optical hardware by model, as a capability rather than a runtime observation.
// MU-01 and MU-02 have no PPG at all; everything from the 2018 Muse 2 onwards
// does. Saying so up front is what lets a heart channel report "this headband
// has no optical sensor" instead of "the sensor stopped working", which are
// different situations and only one of them justifies falling back to a camera.
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

    // PRESET_21 unless we are deliberately asking for optics. MS_03 is the only
    // model with a PRESET_10xx range at all, so it is the only one there is
    // anything to switch to -- has_optical is true for every model from 2018
    // onwards and is deliberately not part of this condition, where it would
    // read as a second gate while being unable to change the outcome.
    //
    // PRESET_1031 rather than one of the 8-channel EEG variants that carry the
    // same 16-channel optics: it keeps EEG at 4 channels, which is the shape
    // EegFrame emits and the channel count the Python side averages over.
    const char* requested = "PRESET_21";
    if (optics_preset_enabled() && model == interaxon::bridge::MuseModel::MS_03) {
        // libMuse documents this as legal after connection: "You can call it in
        // the middle of execute operation, but in this case be aware that this
        // operation will interrupt data streaming to set new preset. Data
        // streaming will be restored after that." (bridge_muse.h:351-358).
        // Whether the headband honours it is a separate question, which is why
        // active_preset() reads the answer back instead of trusting this call.
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
    // Queues and the sample counter belong here, not in stop().
    //
    // stop() runs at process shutdown; disconnect_muse() runs on every headband
    // swap, and connect_named() calls it first -- so the reconnect path is the
    // one that matters and was the one not resetting. Two consequences, both
    // defeating the instrumentation added alongside them: samples kept
    // numbering contiguously across an interval with no headband attached, so
    // the seq gap detector reported nothing lost on precisely the event it
    // exists to catch; and the queues survived the swap, so a reconnect resumed
    // by emitting the previous headband's samples with their old timestamps.
    //
    // optics_dropped resets below, so leaving these alone also left the counter
    // and the sequence disagreeing about what "since when" meant.
    //
    // The EEG queue has always had the same gap on this path. Same line, same
    // hazard, fixed with it rather than left as the one queue that survives a
    // reconnect.
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
    // Leaving them set would report an Athena on PRESET_1031 while nothing is
    // connected, and optical_supported would keep claiming a sensor exists --
    // which is the one field a heart channel would use to decide whether a
    // missing signal justifies falling back to the camera.
    muse_model_.clear();
    requested_preset_.clear();
    optical_supported_ = false;
    latest_bands_ = BandPowers{};
    latest_contact_ = ContactQuality{};
    // Counters included. Carrying them across a reconnect would make "this
    // headband is producing optics" indistinguishable from "some headband did,
    // once, earlier in this process".
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
    // EEG: raw 4-channel samples at 220Hz (PRESET_21).
    // Band absolutes: libMuse computes these from the raw EEG and fires them as
    // separate packet types. All registered before run_asynchronously().
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::EEG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE);
    // Electrode fit/validity reported by the headband itself -- the correct
    // basis for "signal quality", as opposed to inferring it from calmness.
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::NOTCH_FILTERED_EEG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::HSI_PRECISION);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_GOOD);
    // Optical. Registered unconditionally rather than behind MUSE_ENABLE_OPTICS
    // or a model check: a headband that emits neither simply never fires these,
    // which is itself the observation worth having, and gating them would mean
    // "no optics" and "we did not ask for optics" looked identical.
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::OPTICS);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::PPG);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_PPG_GOOD);
    chosen->register_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::IS_HEART_GOOD);
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

// Guarded like firmware_version() above: queue_mutex_ lives inside the
// ENABLE_LIBMUSE block, so an unguarded body would break the OFF build -- which
// is the one CI compiles.
std::string MuseBridgeService::muse_model() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return muse_model_;
#else
    return {};
#endif
}

// Outside the ENABLE_LIBMUSE region, like the accessors below it: main.cpp
// calls this unconditionally, so a definition nested inside the guard links in
// the ON build and leaves an unresolved external in the OFF build CI compiles.
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
    // One call, both fields. Read live rather than cached at request time: the
    // configuration is repopulated when settings change, so a preset that lands
    // a moment after set_preset() still shows up -- and one that never lands
    // keeps reporting the old value, which is the signal worth having.
    //
    // Deliberately outside the lock. get_muse_configuration() is thread-safe on
    // its own, and calling into libMuse while holding the queue mutex would put
    // an SDK call on the path the data listener needs to enqueue frames.
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
    // One list, called from both teardown paths. They previously carried
    // byte-identical copies kept in sync by hand, which is the shape of thing
    // that silently leaks a listener across reconnects the first time someone
    // adds a packet type to one and not the other -- and this change adds four.
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
    };
    for (const auto type : kTypes) {
        muse->unregister_data_listener(data_listener_, type);
    }
}

void MuseBridgeService::update_optical(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    // values() rather than enumerating the Optics/Ppg accessors: it reports how
    // many channels actually arrived, which is the thing worth observing.
    // PRESET_1031 claims 16-channel optics, and asking for OPTICS9..OPTICS16 by
    // name on a device running a 4- or 8-channel mode would read whatever those
    // accessors return for channels that are not present.
    const std::vector<double> values = packet->values();
    const bool is_optics = packet->packet_type() == interaxon::bridge::MuseDataPacketType::OPTICS;
    const long long now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();

    std::lock_guard<std::mutex> lock(queue_mutex_);
    // Clamped to what is actually stored, so the count and the array can never
    // disagree. This field exists to report a channel count nobody knows in
    // advance, which is exactly the situation where a preset delivering more
    // than the buffer holds would otherwise publish "20 channels" beside
    // sixteen numbers.
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

    // Queue the sample itself, not just the counters. OPTICS only -- the PPG
    // packet path is for 2018-2024 hardware and has its own channel mapping;
    // streaming both into one queue would produce a series whose meaning
    // changes with the headband.
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
        // Same bound as the EEG queue. At 64Hz this is ~32 seconds of backlog,
        // which is far longer than the main loop can fall behind without
        // something else being badly wrong.
        //
        // Counted, not just dropped. The derivation rebuilds its clock from
        // sample index, so a silent discard shifts that clock by one interval
        // for the rest of the session and shows up as one impossible beat
        // interval -- which is exactly what the seq field and this counter
        // exist to make visible rather than plausible.
        if (optics_queue_.size() > 2048) {
            optics_queue_.pop();
            latest_optical_.optics_dropped += 1;
        }
    }
}

void MuseBridgeService::update_optical_quality(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    const std::vector<double> values = packet->values();
    if (values.empty()) {
        return;
    }
    // libMuse's own verdict rather than a threshold invented here. Non-zero is
    // good, matching the IS_GOOD convention already used for the electrodes.
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

    // Average only the electrodes the headband says are usable. Averaging all
    // four unconditionally meant a single failed electrode contaminated every
    // band value, and two failed ones (the ear contacts are the usual pair)
    // made the whole spectrum unusable -- even while the two frontal
    // electrodes were reading cleanly. Excluding the bad ones keeps the good
    // channels' data intact instead of discarding the whole frame.
    //
    // Falls back to all four when no contact data has arrived yet, so this
    // never produces less data than the previous behaviour.
    double sum = 0.0;
    int used = 0;
    if (latest_contact_.has_is_good || latest_contact_.has_hsi) {
        for (size_t i = 0; i < per_channel.size(); ++i) {
            const bool valid_data = !latest_contact_.has_is_good || latest_contact_.is_good[i] >= 1.0;
            // HSI: 1 good, 2 mediocre, 4 poor. 0 means "not reported", which
            // is covered by <= 2.0 -- absence of a fit reading is not evidence
            // of a bad fit, so it must not exclude the channel.
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
    // Do NOT call any libMuse API (e.g. get_muse_version) here.
    // libMuse holds an internal lock during callbacks; re-entering the SDK deadlocks.
    // GettingData32 avoids this by posting to the UI thread first.
    //
    // Deliberately does NOT drain the queues or reset optics_seq_, which looks
    // like an omission and is not. Two reasons:
    //
    //  - This fires on every state transition, including transient blips. A
    //    reset here would discard in-flight samples that are perfectly good,
    //    and reset the sequence for a link that never actually went away.
    //  - Every reconnect the sidecar drives goes through connect_named(),
    //    which calls disconnect_muse() first, and that path resets properly.
    //
    // The residual gap is a link libMuse re-establishes entirely on its own,
    // with no connect from above: the sequence would then continue across it
    // and a consumer would not see the interruption. Accepted rather than
    // fixed, because the fix belongs here and doing it here costs more than
    // the case is worth.
    std::lock_guard<std::mutex> lock(queue_mutex_);
    last_connection_state_ = static_cast<int>(state);
    connected_ = (state == interaxon::bridge::ConnectionState::CONNECTED);
    if (state != interaxon::bridge::ConnectionState::CONNECTED) {
        firmware_version_.clear();
    }
}

void MuseBridgeService::refresh_bluetooth_state() {
    // Blocking .get() is safe here: the bridge has no message pump to stall
    // (main() runs winrt::init_apartment() in MTA mode), unlike GettingData32
    // which must never block its UI thread and so only checks this once at
    // startup via an async continuation.
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
    // No Bluetooth radio enumerated: default to enabled so this diagnostic
    // never masks the real cause of a failed scan.
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
