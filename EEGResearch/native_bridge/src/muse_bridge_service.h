#pragma once

#include <array>
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

/**
 * Electrode contact quality straight from libMuse -- the headband's own view
 * of whether each sensor is seated well, independent of what the wearer is
 * doing. This is what "signal quality" should be derived from; deriving it
 * from a calmness/alpha measure conflates "is the headband on properly" with
 * "is the student relaxed", and reports poor contact for a perfectly-fitted
 * headband on an alert, focused student.
 */
struct ContactQuality {
    // HSI_PRECISION per channel (TP9, AF7, AF8, TP10):
    // 1 = good fit, 2 = mediocre, 4 = poor. 0 means "not received yet".
    std::array<double, 4> hsi{{0.0, 0.0, 0.0, 0.0}};
    // IS_GOOD per channel: 1 = last second of EEG was usable, 0 = not.
    std::array<double, 4> is_good{{0.0, 0.0, 0.0, 0.0}};
    bool has_hsi{false};
    bool has_is_good{false};
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
    /** Per-electrode fit/validity as reported by the headband itself. */
    ContactQuality contact_quality() const;

    /** BLE rescan: stop_listening + start_listening (matches GettingData32 Refresh). */
    void refresh_scan();
    /** Connect to a headband by exact name from muse_names(); returns false if not found. */
    bool connect_named(const std::string& name);
    /** Disconnect and drop the active Muse handle. */
    void disconnect_muse();

    /**
     * Whether the Windows Bluetooth radio itself is powered on (matches
     * GettingData32's check_bluetooth_enabled/is_bluetooth_enabled). True
     * when the radio state can't be determined, so this never blocks a scan
     * on its own -- it's a diagnostic surfaced to callers, not a gate.
     */
    bool bluetooth_enabled() const;

private:
    std::atomic<bool> running_;
    long long frame_counter_;
    BandPowers latest_bands_{};
    ContactQuality latest_contact_{};

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
    std::atomic<bool> bluetooth_enabled_{true};

    void enqueue_frame(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    void update_band_power(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    /** Records HSI_PRECISION / IS_GOOD packets into latest_contact_. */
    void update_contact_quality(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    void update_connection_state(interaxon::bridge::ConnectionState state);
    void rebuild_muse_name_list();
    /** Re-queries the OS Bluetooth radio state; called on start() and refresh_scan(). */
    void refresh_bluetooth_state();
#endif
};
