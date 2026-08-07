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
     * The preset the headband reports being on, from MuseConfiguration.
     *
     * An observation, not an echo of the request. libMuse documents the
     * configuration as repopulated "after headband settings (like preset or
     * notch frequency) are changed" (bridge_muse.h:262-265), so this is read
     * live on each call rather than cached at request time -- a preset applied
     * a moment later still shows up.
     *
     * Empty when nothing is connected or the configuration has not arrived.
     */
    std::string active_preset() const;
    /**
     * EEG channel count the headband reports for the current preset.
     *
     * Corroborates active_preset() with something independently observable:
     * PRESET_21 and PRESET_1031 are both 4-channel, so a jump to 8 would mean
     * a preset nobody asked for. 0 when unknown.
     */
    int eeg_channel_count() const;
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
