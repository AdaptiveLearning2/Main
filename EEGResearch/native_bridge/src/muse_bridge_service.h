#pragma once

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
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

/**
 * One optical sample, stamped with the packet's own time, not arrival time.
 * `n` channels is preset-dependent: 4 on PRESET_1035, 8 on 1033, 16 on the modes that break the link.
 */
struct OpticsFrame {
    long long mono_ts_ms;
    /**
     * Monotonic sample number, never reused. mono_ts_ms carries ~25ms of BLE batching
     * jitter, so the time base is rebuilt from this; a gap means a dropped sample.
     */
    long long seq{0};
    std::array<double, 16> ch{};
    int n{0};
};

struct BandPowers {
    double delta{0.0};
    double theta{0.0};
    double alpha{0.0};
    double beta{0.0};
    double gamma{0.0};
};

/** Electrode contact quality from libMuse: the headband's view of fit, not of the wearer's state. */
struct ContactQuality {
    // HSI_PRECISION per channel (TP9, AF7, AF8, TP10): 1 good, 2 mediocre, 4 poor, 0 not received.
    std::array<double, 4> hsi{{0.0, 0.0, 0.0, 0.0}};
    // IS_GOOD per channel: 1 = last second of EEG was usable, 0 = not.
    std::array<double, 4> is_good{{0.0, 0.0, 0.0, 0.0}};
    bool has_hsi{false};
    bool has_is_good{false};
};

/**
 * Optical counters and most-recent sample. OPTICS and PPG are counted separately:
 * the 2025 Athena carries PPG inside OPTICS, while 2018-2024 models emit PPG packets only.
 */
struct OpticalSignals {
    long long optics_packets{0};
    long long ppg_packets{0};
    /** values_size() of the most recent packet of each kind. 0 before any. */
    int optics_values{0};
    int ppg_values{0};
    /** Most recent sample. OPTICS in microamps, PPG arbitrary units. */
    std::array<double, 16> last_optics{};
    std::array<double, 3> last_ppg{};
    /** libMuse's quality verdicts; has_ flags separate "bad" from "not reported yet". */
    bool ppg_good{false};
    bool heart_good{false};
    bool has_ppg_good{false};
    bool has_heart_good{false};
    /** Samples dropped on a full queue; non-zero means RMSSD across the gap is wrong. */
    long long optics_dropped{0};
    /** steady_clock ms of the latest optical packet, 0 if none; process-local, see optical_age_ms(). */
    long long last_ms{0};
};

/** The headband's own account of its settings; `known` separates "no reading yet" from a real zero. */
struct DeviceConfig {
    std::string preset;
    int eeg_channel_count{0};
    bool known{false};
};

/**
 * Recovery state for a link that dropped on its own.
 * `reconnecting`: backoff or attempt in flight. `exhausted`: a person must click Connect.
 */
struct ReconnectStatus {
    bool enabled{false};
    bool reconnecting{false};
    int attempt{0};
    int max_attempts{0};
    bool exhausted{false};
    /** ms since this connection's last EEG packet; -1 when not connected or none yet. */
    long long eeg_age_ms{-1};
};

class MuseBridgeService {
public:
    MuseBridgeService();
    ~MuseBridgeService();

    bool start();
    void stop();
    bool poll_frame(EegFrame& frame);
    /**
     * Drain one optical sample. Non-blocking, unlike poll_frame; own queue (64Hz vs 256Hz EEG).
     * If EEG stops while optics continue, optics arrive in 200ms batches (poll_frame's wait).
     */
    bool poll_optics(OpticsFrame& frame);

    /** "synthetic" (no libMuse) or "libmuse" when compiled with ENABLE_LIBMUSE. */
    const char* bridge_mode() const noexcept;
    bool is_muse_connected() const;
    bool is_muse_discovered() const;
    /** interaxon::bridge::ConnectionState as int; -1 in a synthetic build. */
    int connection_state() const;

    /** Last scan results (LibMuse device names). Empty when not using LibMuse. */
    std::vector<std::string> muse_names() const;
    std::string active_muse_name() const;
    std::string firmware_version() const;
    /**
     * Model from libMuse, e.g. "MS-03" (2025 Muse S Athena); empty until CONNECTED.
     * Read from the device, not the user-settable name.
     */
    std::string muse_model() const;
    /** The preset asked for, e.g. "PRESET_1031"; compare with active_preset to spot an ignored request. */
    std::string requested_preset() const;
    /** Read live from MuseConfiguration, preset and channel count in one call so they can't mismatch. */
    DeviceConfig device_config() const;
    OpticalSignals optical_signals() const;
    /** ms since the latest optical packet, or -1 if none; tells a live stream from one that stopped. */
    long long optical_age_ms() const;
    /** Whether the headband has an optical (PPG/fNIRS) sensor at all; Muse 2016 has none. */
    bool optical_supported() const;
    /**
     * Charge 0-100, or negative before the first BATTERY packet (libMuse sends it periodically,
     * not on connect). Negative, not 0, since 0% is a real reading; main.cpp emits null.
     */
    double battery_percent() const;
    BandPowers band_powers() const;
    ContactQuality contact_quality() const;
    /** Electrodes averaged into the latest band values (4 = all usable); 0 before any band packet. */
    int band_channels_used() const;
    /**
     * True while notch-filtered EEG (45-65Hz removed) arrived within NOTCH_STALE_MS.
     * Not a latch: a latch would suppress raw EEG forever if notch packets stopped.
     */
    bool notch_available() const;
    void note_notch_available();
    /** How long a notch packet keeps raw EEG suppressed; notch arrives well under 1s apart. */
    static constexpr long long NOTCH_STALE_MS = 2000;

    /** BLE rescan: stop_listening + start_listening (matches GettingData32 Refresh). */
    void refresh_scan();
    /** Connect to a headband by exact name from muse_names(); returns false if not found. */
    bool connect_named(const std::string& name);
    /** Disconnect and drop the active Muse handle. */
    void disconnect_muse();

    /**
     * Call once per main-loop iteration. Runs the liveness watchdog (a BLE link can stop
     * delivering while the SDK still says CONNECTED) and launches/judges backed-off
     * connect_named() attempts after an unexpected drop.
     */
    void service_auto_reconnect();
    /** Abandon any reconnect in progress; called on every person-sent command. */
    void cancel_auto_reconnect();
    ReconnectStatus reconnect_status() const;
    /** Attempts before giving up; mirrored by the frontend's cap. */
    static constexpr int MAX_RECONNECT_ATTEMPTS = 5;

    /** Whether the Windows Bluetooth radio is on; true when unknown. A diagnostic, not a gate. */
    bool bluetooth_enabled() const;

private:
    /** Pick the preset for a model; runs on CONNECTED since get_model() returns MU_02 before that. */
#if defined(ENABLE_LIBMUSE)
    void apply_model_preset(const std::shared_ptr<interaxon::bridge::Muse>& muse);
    /** Clears everything describing the headband. Call with queue_mutex_ held. */
    void reset_device_fields_locked();
#endif

    std::atomic<bool> running_;
    long long frame_counter_;
    BandPowers latest_bands_{};
    ContactQuality latest_contact_{};
    OpticalSignals latest_optical_{};
    int band_channels_used_{0};
    std::string muse_model_;
    std::string requested_preset_;
    bool optical_supported_{false};
    double battery_percent_{-1.0};
    // steady_clock ms of the last notch-filtered packet; 0 = none yet.
    std::atomic<long long> last_notch_ms_{0};

    // ── auto-reconnect ──────────────────────────────────────────────────
    // Atomics, not under queue_mutex_: the connection callback already holds it when it writes these.
    // Watchdog measures from the later of these two, so a preset switch's pause isn't a dead link.
    std::atomic<long long> last_any_eeg_ms_{0};
    std::atomic<long long> connected_since_ms_{0};
    std::atomic<bool> reconnect_armed_{false};
    // connect_named() launched, CONNECTED not yet arrived.
    std::atomic<bool> reconnect_in_flight_{false};
    std::atomic<bool> reconnect_exhausted_{false};
    std::atomic<int> reconnect_attempt_{0};
    std::atomic<long long> next_reconnect_at_ms_{0};
    std::atomic<long long> attempt_started_ms_{0};
    // Bumped on cancel; a reconnect thread undoes its connect if this moved meanwhile.
    std::atomic<int> reconnect_generation_{0};
    std::atomic<bool> reconnect_thread_done_{true};
    std::thread reconnect_thread_;
    // Not cleared by reset_device_fields_locked(): must survive disconnect. Guarded by queue_mutex_.
    std::string last_connected_name_;

    void arm_reconnect();
    /** Schedule the next attempt after a backoff, or mark exhausted. Safe from any thread. */
    void schedule_next_reconnect();
    void launch_reconnect_attempt();

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
    std::queue<OpticsFrame> optics_queue_;
    /** Never reset within a connection; reset on disconnect so a new headband starts fresh. */
    long long optics_seq_{0};
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
    /** Unregisters every registered packet type; shared by both teardown paths. */
    void unregister_data_listeners(const std::shared_ptr<interaxon::bridge::Muse>& muse);
    /** Records OPTICS / PPG packets into latest_optical_. */
    void update_optical(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    /** Records IS_PPG_GOOD / IS_HEART_GOOD into latest_optical_. */
    void update_optical_quality(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    /** Records BATTERY into battery_percent_. */
    void update_battery(const std::shared_ptr<interaxon::bridge::MuseDataPacket>& packet);
    void update_connection_state(interaxon::bridge::ConnectionState state);
    void rebuild_muse_name_list();
    /** Re-queries the OS Bluetooth radio state; called on start() and refresh_scan(). */
    void refresh_bluetooth_state();
#endif
};
