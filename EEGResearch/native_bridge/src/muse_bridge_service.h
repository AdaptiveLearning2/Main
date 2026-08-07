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

/**
 * One optical sample, as it left the headband.
 *
 * Carries the packet's own timestamp rather than an arrival time. Beat-to-beat
 * intervals are the whole point of RMSSD, and at 64Hz the sample spacing is
 * ~15.6ms -- the same order as the HRV differences being measured -- so timing
 * jitter introduced by when this process happened to be scheduled would land
 * directly in the output.
 *
 * `n` is how many channels arrived, since that is preset-dependent: 4 on
 * PRESET_1035, 8 on 1033, 16 on the modes that break the link.
 */
struct OpticsFrame {
    long long mono_ts_ms;
    /**
     * Monotonic sample number, assigned at enqueue and never reused.
     *
     * The derivation reconstructs its time base from sample index rather than
     * from mono_ts_ms, because the stamps carry ~25ms rms of BLE batching
     * jitter and RMSSD measures 20-50ms differences. That reconstruction is
     * only sound if no sample goes missing, and three places can drop one --
     * the queue bound below, a WSAEWOULDBLOCK in the TCP server, and a short
     * send(). None is observable from the samples themselves: a lost sample
     * just shifts the reconstructed clock by one interval for the remainder of
     * the recording, and shows up as a single impossible beat interval.
     *
     * A gap in this sequence makes all three detectable for free.
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

/**
 * Evidence that the optical sensor is producing data, and what it looks like.
 *
 * Deliberately counters and a most-recent sample rather than a stream. The
 * question this answers is "does a PRESET_1031 Athena actually emit OPTICS, at
 * what rate, and with how many channels" -- and until that has been seen, any
 * streaming format for it would be designed against an assumption. The stream
 * comes next, once the shape is known rather than inferred.
 *
 * OPTICS and PPG are counted separately because they are different packets on
 * different hardware: the 2025 Athena carries PPG inside OPTICS and emits no
 * PPG packet at all, while 2018-2024 models do the opposite. A build that saw
 * only a total would not be able to tell which one it was talking to.
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
    /** libMuse's own quality verdicts, so this bridge does not have to invent
     *  one. Absent until the corresponding packet type arrives, which is why
     *  each carries a has_ flag rather than defaulting to false -- "the sensor
     *  says the signal is bad" and "the sensor has not said anything" are
     *  different, and only one of them justifies a fallback. */
    bool ppg_good{false};
    bool heart_good{false};
    bool has_ppg_good{false};
    bool has_heart_good{false};
    /**
     * Optical samples discarded because the queue was full.
     *
     * Non-zero means the reconstructed time base has lost alignment, and any
     * RMSSD computed across the gap is wrong. Reported rather than merely
     * bounded, because a silent drop is indistinguishable from a slow heart.
     */
    long long optics_dropped{0};
    /** steady_clock ms of the most recent optical packet of either kind; 0 if
     *  none. Not emitted raw -- steady_clock's epoch is arbitrary and
     *  process-local, so the number means nothing outside this process.
     *  optical_age_ms() turns it into the thing a consumer actually wants. */
    long long last_ms{0};
};

/**
 * The headband's own account of its current settings.
 *
 * `known` is separate rather than being encoded as preset=="" or channels==0,
 * for the same reason ContactQuality carries has_hsi: zero is a value, and a
 * consumer that branches on it cannot otherwise tell "no headband", "the
 * configuration has not arrived yet" and "the OFF build" apart from each other
 * or from a real reading. Nothing branches on it today; this is what stops the
 * first thing that does from having to guess.
 */
struct DeviceConfig {
    std::string preset;
    int eeg_channel_count{0};
    bool known{false};
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
     * Separate queue rather than interleaving into eeg_queue_: the two run at
     * different rates (256Hz EEG, 64Hz optics) and a consumer wants them as
     * separate streams.
     *
     * Non-blocking so this never adds a second wait of its own -- but optics
     * latency is still bounded by poll_frame's 200ms wait, because both are
     * drained from the same loop. At 256Hz EEG keeps that loop spinning and the
     * bound is never approached. It matters in the case where EEG stops while
     * optics continues, which is not hypothetical: electrode contact
     * collapsing to [4,4,4,4] on an optics-carrying preset is a documented
     * failure mode. Optics then emerge in 200ms batches.
     *
     * Not a correctness problem -- each sample carries its own timestamp and
     * sequence number, and the queue holds ~32s -- but the coupling is real
     * and worth knowing before someone measures latency and is surprised.
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
     * Reported rather than inferred from the name, because the name is
     * user-settable and the model decides which presets are available at all.
     */
    std::string muse_model() const;
    /**
     * The preset this bridge asked for, e.g. "PRESET_1031". An intent.
     *
     * Reported next to active_preset() rather than instead of it, because the
     * two disagreeing is the whole diagnosis: set_preset() returns void, so a
     * request that the headband ignores is indistinguishable from one it
     * honoured unless the result is read back.
     */
    std::string requested_preset() const;
    /**
     * What the headband reports about itself, read from MuseConfiguration.
     *
     * Observations, not an echo of what was requested. libMuse documents the
     * configuration as repopulated "after headband settings (like preset or
     * notch frequency) are changed" (bridge_muse.h:262-265), so this is read
     * live rather than cached at request time -- a preset applied a moment
     * later still shows up.
     *
     * Returned together, from a single get_muse_configuration() call, because
     * they are two views of one thing. Fetching them separately means a preset
     * change landing between the two calls puts a mismatched pair on the wire
     * -- in the one-second window right after a switch, which is exactly when
     * someone is watching.
     */
    DeviceConfig device_config() const;
    /** Optical packet counters, latest sample and libMuse's quality verdicts. */
    OpticalSignals optical_signals() const;
    /**
     * Milliseconds since the most recent optical packet, or -1 if none yet.
     *
     * The counters alone cannot distinguish a stream that is flowing from one
     * that delivered a burst and stopped -- both leave a non-zero count. This
     * is what makes "is optics live right now" answerable, which is the whole
     * point of reporting a rate.
     */
    long long optical_age_ms() const;
    /**
     * Whether the headband exposes an optical (PPG/fNIRS) sensor at all.
     *
     * A capability, not a dropout. Muse 2016 has no optical hardware, and a
     * heart channel that reports "sensor failed" for a device that never had
     * one would hand the camera fallback a job it should not be given.
     */
    bool optical_supported() const;
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
     * Deliberately not a latch. Latching on the first notch packet means raw
     * EEG stays suppressed forever if notch packets later stop, and since raw
     * is the only fallback, the bridge then enqueues nothing at all until a
     * reconnect: a full outage, recoverable only by cycling the headband.
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
     * Separate from connect_named because get_model() is documented to return
     * MU_02 until the headband reaches CONNECTED, and the preset is set before
     * that -- so this runs from the connection listener instead.
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
    // steady_clock ms at which the last notch-filtered packet arrived; 0 means
    // none yet. Not a bool: see notch_available().
    std::atomic<long long> last_notch_ms_{0};

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
    /** Never reset within a connection; reset with the rest on disconnect, so
     *  a reconnect starts a fresh sequence rather than appearing to continue. */
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
    void update_connection_state(interaxon::bridge::ConnectionState state);
    void rebuild_muse_name_list();
    /** Re-queries the OS Bluetooth radio state; called on start() and refresh_scan(). */
    void refresh_bluetooth_state();
#endif
};
