#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <vector>

#if defined(ENABLE_LIBMUSE)
#include "muse.h"
#endif

struct EegFrame {
    long long mono_ts_ms;
    double tp9;
    double af7;
    double af8;
    double tp10;
};

struct BandPowers {
    double delta{0.0};
    double theta{0.0};
    double alpha{0.0};
    double beta{0.0};
    double gamma{0.0};
};

class MuseBridgeService {
public:
    MuseBridgeService();
    ~MuseBridgeService();

    bool start();
    void stop();
    bool poll_frame(EegFrame& frame);

    /** "synthetic" (no libMuse) or "libmuse" when compiled with ENABLE_LIBMUSE. */
    const char* bridge_mode() const noexcept;
    bool is_muse_connected() const;
    bool is_muse_discovered() const;
    /**
     * interaxon::bridge::ConnectionState as int, or -1 when not applicable
     * (synthetic / non-libMuse build).
     */
    int connection_state() const;

    /** Last scan results (LibMuse device names). Empty when not using LibMuse. */
    std::vector<std::string> muse_names() const;
    std::string active_muse_name() const;
    std::string firmware_version() const;
    BandPowers band_powers() const;

    /** BLE rescan: stop_listening + start_listening (matches GettingData32 Refresh). */
    void refresh_scan();
    /** Connect to a headband by exact name from muse_names(); returns false if not found. */
    bool connect_named(const std::string& name);
    /** Disconnect and drop the active Muse handle. */
    void disconnect_muse();

private:
    std::atomic<bool> running_;
    long long frame_counter_;
    BandPowers latest_bands_{};

#if defined(ENABLE_LIBMUSE)
    class BridgeMuseListener;
    class BridgeDataListener;
    class BridgeConnectionListener;
    friend class BridgeMuseListener;
    friend class BridgeDataListener;
    friend class BridgeConnectionListener;

    std::shared_ptr<interaxon::bridge::MuseManagerWindows> manager_;
    std::shared_ptr<interaxon::bridge::Muse> active_muse_;
    std::shared_ptr<BridgeMuseListener> muse_listener_;
    std::shared_ptr<BridgeDataListener> data_listener_;
    std::shared_ptr<BridgeConnectionListener> connection_listener_;

    mutable std::mutex queue_mutex_;
    std::condition_variable queue_cv_;
    std::queue<EegFrame> eeg_queue_;
    bool connected_{false};
    bool discovered_{false};
    int last_connection_state_{0};
    std::vector<std::string> muse_names_;
    std::string active_muse_name_;
    std::string firmware_version_;

    void enqueue_frame(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    void update_band_power(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    void update_connection_state(interaxon::bridge::ConnectionState state);
    void rebuild_muse_name_list();
#endif
};
