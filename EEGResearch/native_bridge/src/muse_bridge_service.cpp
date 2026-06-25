#include "muse_bridge_service.h"

#include <chrono>
#include <cmath>
#include <iostream>
#include <thread>

#if defined(ENABLE_LIBMUSE)
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
        case interaxon::bridge::MuseDataPacketType::EEG:
            service_.enqueue_frame(packet);
            break;
        case interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE:
        case interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE:
            service_.update_band_power(packet);
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
                                        const std::shared_ptr<interaxon::bridge::Muse>&) override {
        service_.update_connection_state(packet.current_connection_state);
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
            muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::EEG);
            muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE);
            muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE);
            muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE);
            muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE);
            muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE);
            muse->unregister_connection_listener(connection_listener_);
        }
    }
    if (manager_) {
        manager_->stop_listening();
        manager_.reset();
    }
    {
        std::lock_guard<std::mutex> lock(queue_mutex_);
        while (!eeg_queue_.empty()) {
            eeg_queue_.pop();
        }
        muse_names_.clear();
        discovered_ = false;
        active_muse_name_.clear();
        firmware_version_.clear();
        latest_bands_ = BandPowers{};
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
    manager_->stop_listening();
    manager_->start_listening();
#endif
}

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
        active_muse_name_.clear();
        firmware_version_.clear();
        latest_bands_ = BandPowers{};
        connected_ = false;
        last_connection_state_ = static_cast<int>(interaxon::bridge::ConnectionState::DISCONNECTED);
    }
    if (muse) {
        muse->disconnect();
        wait_for_disconnect(muse);
        muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::EEG);
        muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::DELTA_ABSOLUTE);
        muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::THETA_ABSOLUTE);
        muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::ALPHA_ABSOLUTE);
        muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::BETA_ABSOLUTE);
        muse->unregister_data_listener(data_listener_, interaxon::bridge::MuseDataPacketType::GAMMA_ABSOLUTE);
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

BandPowers MuseBridgeService::band_powers() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return latest_bands_;
#else
    return BandPowers{};
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

void MuseBridgeService::update_band_power(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet) {
    const double avg = (packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG1) +
                        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG2) +
                        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG3) +
                        packet->get_eeg_channel_value(interaxon::bridge::Eeg::EEG4)) /
                       4.0;

    std::lock_guard<std::mutex> lock(queue_mutex_);
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
    std::lock_guard<std::mutex> lock(queue_mutex_);
    last_connection_state_ = static_cast<int>(state);
    connected_ = (state == interaxon::bridge::ConnectionState::CONNECTED);
    if (state != interaxon::bridge::ConnectionState::CONNECTED) {
        firmware_version_.clear();
    }
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

int MuseBridgeService::connection_state() const {
#if defined(ENABLE_LIBMUSE)
    std::lock_guard<std::mutex> lock(queue_mutex_);
    return last_connection_state_;
#else
    return -1;
#endif
}
