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
 * One optical sample, as it left the headband.
 *
 * Carries the packet's own timestamp, not an arrival time: RMSSD measures
 * 20-50ms beat intervals, and at 64Hz sample spacing (~15.6ms) is the same
 * order, so scheduling jitter in this process would otherwise leak in.
 *
 * `n` is how many channels arrived, since that is preset-dependent: 4 on
 * PRESET_1035, 8 on 1033, 16 on the modes that break the link.
 */
struct OpticsFrame {
    long long mono_ts_ms;
    /**
     * Monotonic sample number, assigned at enqueue and never reused.
     *
     * mono_ts_ms carries ~25ms of BLE batching jitter, so the time base is
     * reconstructed from this index instead. That only works if no sample is
     * dropped -- possible at the queue bound below, a WSAEWOULDBLOCK in the
     * TCP server, or a short send() -- and a gap in this sequence is what
     * makes any of those three detectable.
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

/**
 * Electrode contact quality straight from libMuse -- the headband's own view
 * of fit, independent of what the wearer is doing. Deriving quality from a
 * calmness/alpha measure instead would conflate "is the headband on
 * properly" with "is the student relaxed".
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

/**
 * Evidence that the optical sensor is producing data, and what it looks like.
 * Counters and a most-recent sample, not a stream -- the open question is
 * whether a given preset emits OPTICS at all, at what rate, and how many
 * channels, so the streaming format waits until that's known.
 *
 * OPTICS and PPG are counted separately because they're different packets on
 * different hardware: the 2025 Athena carries PPG inside OPTICS and emits no
 * separate PPG packet, while 2018-2024 models do the opposite.
 */
struct OpticalSignals {
    long long optics_packets{0};
    long long ppg_packets{0};
    /** values_size() of the most recent packet of each kind. 0 before any. */
    int optics_values{0};
    int ppg_values{0};
    /** Most recent sample, truncated to what arrived. Units are microamps for
     *  OPTICS and arbitrary for PPG, per bridge_optics.h / bridge_ppg.h. */
    std::array<double, 16> last_optics{};
    std::array<double, 3> last_ppg{};
    /** libMuse's own quality verdicts. has_ flags because "signal is bad" and
     *  "sensor hasn't reported yet" are different, and only one justifies a
     *  fallback. */
    bool ppg_good{false};
    bool heart_good{false};
    bool has_ppg_good{false};
    bool has_heart_good{false};
    /**
     * Optical samples discarded because the queue was full. Non-zero means
     * the reconstructed time base has lost alignment and any RMSSD across the
     * gap is wrong -- reported rather than silently bounded, since a silent
     * drop looks identical to a slow heart.
     */
    long long optics_dropped{0};
    /** steady_clock ms of the most recent optical packet; 0 if none. Not
     *  emitted raw since that clock is process-local. optical_age_ms() turns
     *  it into something a consumer can actually use. */
    long long last_ms{0};
};

/**
 * The headband's own account of its current settings.
 *
 * `known` is separate from preset=="" or channels==0, same reasoning as
 * ContactQuality's has_hsi: zero is a valid value, so a consumer needs a way
 * to tell "no headband" apart from "no reading yet" or a real zero.
 */
struct DeviceConfig {
    std::string preset;
    int eeg_channel_count{0};
    bool known{false};
};

/**
 * Where the bridge is in recovering a link that dropped on its own.
 *
 * `reconnecting` covers both waiting out a backoff and an attempt in flight;
 * `exhausted` means every attempt failed and a person has to click Connect.
 * The two are separate from muse_connected: while reconnecting the link is
 * down, and a consumer that only read muse_connected would tell the student
 * the headband is gone when it is about to come back.
 */
struct ReconnectStatus {
    bool enabled{false};
    bool reconnecting{false};
    int attempt{0};
    int max_attempts{0};
    bool exhausted{false};
    /** ms since the last EEG packet on this connection, or -1 when not
     *  connected or nothing has arrived since connecting. */
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
     * Drain one optical sample. Non-blocking, unlike poll_frame.
     *
     * Its own queue rather than sharing eeg_queue_, since EEG and optics run
     * at different rates (256Hz vs 64Hz) and a consumer wants them separate.
     *
     * Optics latency is bounded by poll_frame's 200ms wait, since both are
     * drained from the same loop -- normally not noticeable at 256Hz EEG, but
     * if EEG stops while optics keeps going (electrode contact collapsing on
     * an optics-carrying preset is a known failure mode), optics arrive in
     * 200ms batches. Not a correctness issue -- each sample has its own
     * timestamp and sequence number, and the queue holds ~32s -- but worth
     * knowing if latency looks surprising.
     */
    bool poll_optics(OpticsFrame& frame);

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
    /**
     * Headband model as reported by libMuse once connected, e.g. "MS-03" for a
     * 2025 Muse S Athena. Empty until the CONNECTED packet arrives.
     *
     * Read from the device rather than inferred from its name, since the name
     * is user-settable and the model decides which presets are available.
     */
    std::string muse_model() const;
    /**
     * The preset this bridge asked for, e.g. "PRESET_1031".
     *
     * Reported next to active_preset() because the two disagreeing is the
     * diagnosis: set_preset() returns void, so a request the headband ignored
     * is only visible by comparing intent against the read-back result.
     */
    std::string requested_preset() const;
    /**
     * What the headband reports about itself, read live from
     * MuseConfiguration (which libMuse repopulates after preset or notch
     * changes), not cached at request time -- so a preset applied a moment
     * later still shows up.
     *
     * preset and channel count come from one get_muse_configuration() call so
     * a preset change landing mid-fetch can't put a mismatched pair on the
     * wire, right when someone is watching.
     */
    DeviceConfig device_config() const;
    /** Optical packet counters, latest sample and libMuse's quality verdicts. */
    OpticalSignals optical_signals() const;
    /**
     * Milliseconds since the most recent optical packet, or -1 if none yet.
     * The counters alone can't tell a live stream from one that delivered a
     * burst and stopped -- both leave a non-zero count; this can.
     */
    long long optical_age_ms() const;
    /**
     * Whether the headband exposes an optical (PPG/fNIRS) sensor at all.
     * A capability, not a dropout: Muse 2016 has no optical hardware, and
     * reporting "sensor failed" for a device that never had one would hand
     * the camera fallback a job it shouldn't get.
     */
    bool optical_supported() const;
    /**
     * Charge remaining, 0-100, or **negative when no BATTERY packet has
     * arrived** -- the state for most of the first minute of a session, since
     * libMuse fires this periodically rather than on connect.
     *
     * Negative, not 0, since 0% is a real and alarming reading. main.cpp
     * turns it into JSON null, same rule as eeg_channel_count.
     */
    double battery_percent() const;
    BandPowers band_powers() const;
    /** Per-electrode fit/validity as reported by the headband itself. */
    ContactQuality contact_quality() const;
    /**
     * How many electrodes were averaged into the most recent band values.
     * 4 means all were usable; a lower number means the rest were excluded as
     * badly seated or invalid. 0 before any band packet has arrived.
     */
    int band_channels_used() const;
    /**
     * True while libMuse is *currently* delivering notch-filtered EEG
     * (45-65Hz removed) -- i.e. one arrived within NOTCH_STALE_MS.
     *
     * Not a latch: latching on the first notch packet would suppress raw EEG
     * forever if notch packets later stopped, leaving nothing to enqueue
     * until a reconnect -- a full outage over one missed packet.
     */
    bool notch_available() const;
    /** Called by the data listener each time a notch-filtered packet lands. */
    void note_notch_available();
    /** How long a notch packet keeps raw EEG suppressed. Notch arrives at the
     *  same rate as raw EEG (well under 1s apart), so this is generous. */
    static constexpr long long NOTCH_STALE_MS = 2000;

    /** BLE rescan: stop_listening + start_listening (matches GettingData32 Refresh). */
    void refresh_scan();
    /** Connect to a headband by exact name from muse_names(); returns false if not found. */
    bool connect_named(const std::string& name);
    /** Disconnect and drop the active Muse handle. */
    void disconnect_muse();

    /**
     * Drive the automatic reconnect. Call once per main-loop iteration; it
     * returns immediately unless something is due.
     *
     * Two jobs. First, a liveness watchdog: libMuse reports a drop through
     * receive_muse_connection_packet, but a BLE link can also just stop
     * delivering while the SDK still says CONNECTED, and nothing else here
     * would notice -- NOTCH_STALE_MS only arbitrates raw-vs-notch packet
     * selection. Second, the reconnect itself: an unexpected drop arms a
     * bounded, backed-off sequence of connect_named() calls against the last
     * headband, and this is where they are launched and judged.
     *
     * The preset is not carried across: apply_model_preset() re-derives it
     * from the environment on every CONNECTED, so a reconnect lands on
     * exactly the configuration the process was launched with.
     */
    void service_auto_reconnect();
    /**
     * Abandon any reconnect in progress. Called on every command a person
     * sends -- connect, disconnect, refresh -- because a click means they are
     * taking over, and a reconnect landing after a deliberate disconnect
     * would pair a headband the student just asked to release.
     */
    void cancel_auto_reconnect();
    ReconnectStatus reconnect_status() const;
    /** Attempts before giving up. Mirrored by the frontend's own cap so the
     *  two never disagree about how long is too long. */
    static constexpr int MAX_RECONNECT_ATTEMPTS = 5;

    /**
     * Whether the Windows Bluetooth radio itself is powered on (matches
     * GettingData32's check_bluetooth_enabled/is_bluetooth_enabled). True
     * when the radio state can't be determined, so this never blocks a scan
     * on its own -- it's a diagnostic surfaced to callers, not a gate.
     */
    bool bluetooth_enabled() const;

private:
    /**
     * Pick the preset for a model, once the model is actually known.
     *
     * Separate from connect_named because get_model() returns MU_02 until the
     * headband reaches CONNECTED, so this runs from the connection listener
     * instead, after the real model is known.
     */
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
    // -1 until a BATTERY packet arrives. See battery_percent().
    double battery_percent_{-1.0};
    // steady_clock ms at which the last notch-filtered packet arrived; 0 means
    // none yet. Not a bool: see notch_available().
    std::atomic<long long> last_notch_ms_{0};

    // ── auto-reconnect ──────────────────────────────────────────────────
    // Atomics, not fields under queue_mutex_: they are read on the main loop
    // and written from the libMuse connection callback and the reconnect
    // thread, and the callback already holds queue_mutex_ when it runs.
    //
    // steady_clock ms of the last EEG packet on the current connection, and
    // of the CONNECTED transition. Both 0 when not connected. The watchdog
    // measures from whichever is later, so a fresh connection whose preset
    // switch briefly interrupts streaming is not mistaken for a dead one.
    std::atomic<long long> last_any_eeg_ms_{0};
    std::atomic<long long> connected_since_ms_{0};
    // A drop was noticed and an attempt is scheduled for next_reconnect_at_ms_.
    std::atomic<bool> reconnect_armed_{false};
    // connect_named() has been launched and CONNECTED has not arrived yet.
    std::atomic<bool> reconnect_in_flight_{false};
    std::atomic<bool> reconnect_exhausted_{false};
    std::atomic<int> reconnect_attempt_{0};
    std::atomic<long long> next_reconnect_at_ms_{0};
    std::atomic<long long> attempt_started_ms_{0};
    // Bumped by cancel_auto_reconnect(). A reconnect thread compares the
    // value it launched under against this after connect_named() returns,
    // and undoes its own connect if a person cancelled meanwhile.
    std::atomic<int> reconnect_generation_{0};
    std::atomic<bool> reconnect_thread_done_{true};
    std::thread reconnect_thread_;
    // The headband to reconnect to. Deliberately not cleared by
    // reset_device_fields_locked(), which runs on every disconnect -- that is
    // exactly when this has to survive. Guarded by queue_mutex_.
    std::string last_connected_name_;

    void arm_reconnect();
    /** Schedules the next attempt after a backoff, or marks the sequence
     *  exhausted. Safe from any thread. */
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
    /** Never reset within a connection. Cleared by reset_device_fields_locked(),
     *  called from disconnect_muse() so a new headband starts a fresh sequence
     *  instead of continuing the previous one's. */
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
    /** Unregisters every data packet type this service registers, from both
     *  teardown paths. One list so the two cannot drift apart. */
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
