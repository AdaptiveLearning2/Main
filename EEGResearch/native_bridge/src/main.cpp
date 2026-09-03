#include "bridge_tcp_server.h"
#include "muse_bridge_service.h"

#include <atomic>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <csignal>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

#if defined(ENABLE_LIBMUSE)
#include <winrt/Windows.Foundation.h>
#endif

namespace {
std::atomic<bool> g_keep_running{true};

void append_json_quoted_string(std::ostringstream& o, const std::string& s) {
    o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') {
            o << '\\' << static_cast<char>(c);
        } else if (c < 32) {
            o << ' ';
        } else {
            o << static_cast<char>(c);
        }
    }
    o << '"';
}

void append_bridge_device_fields(std::ostringstream& o, const MuseBridgeService& svc,
                                 const BridgeTcpServer* server = nullptr) {
    if (server) {
        // Lines dropped by the transport, distinct from samples dropped by
        // the queue. Neither shows up in the samples themselves.
        o << ",\"tcp_dropped_lines\":" << server->dropped_lines();
    }
    o << ",\"bridge_mode\":\"" << svc.bridge_mode() << "\""
      << ",\"muse_connected\":" << (svc.is_muse_connected() ? "true" : "false")
      << ",\"muse_discovered\":" << (svc.is_muse_discovered() ? "true" : "false")
      << ",\"bluetooth_enabled\":" << (svc.bluetooth_enabled() ? "true" : "false")
      << ",\"connection_state\":" << svc.connection_state();

    o << ",\"muse_devices\":[";
    const auto names = svc.muse_names();
    for (size_t i = 0; i < names.size(); ++i) {
        if (i > 0) {
            o << ',';
        }
        append_json_quoted_string(o, names[i]);
    }
    o << "],\"active_muse_name\":";
    append_json_quoted_string(o, svc.active_muse_name());
    o << ",\"firmware_version\":";
    append_json_quoted_string(o, svc.firmware_version());
    // optical_supported tells a consumer a headband with no PPG hardware
    // apart from one whose PPG just stopped.
    o << ",\"muse_model\":";
    append_json_quoted_string(o, svc.muse_model());
    // requested_preset is what we asked for; active_preset (below) is what the
    // headband reports back. set_preset() returns void, so comparing the two
    // is the only way to notice a request the device ignored.
    o << ",\"requested_preset\":";
    append_json_quoted_string(o, svc.requested_preset());
    // One fetch for both fields so a preset change mid-status can't split a
    // preset and channel count from either side of the change onto one line.
    const DeviceConfig device = svc.device_config();
    o << ",\"active_preset\":";
    append_json_quoted_string(o, device.preset);
    // null, not 0, when configuration is unknown -- 0 could be a real count.
    o << ",\"eeg_channel_count\":";
    if (device.known) {
        o << device.eeg_channel_count;
    } else {
        o << "null";
    }
    o << ",\"optical_supported\":" << (svc.optical_supported() ? "true" : "false");
    // null until the first BATTERY packet arrives (libMuse fires it on its own
    // schedule, not on connect) -- most of the first minute of a session.
    // null, not 0: 0% is a real reading a student needs to act on.
    o << ",\"battery_percent\":";
    const double battery = svc.battery_percent();
    if (battery >= 0.0) {
        o << battery;
    } else {
        o << "null";
    }

    // Optical evidence: counts, latest sample, and libMuse's own quality
    // verdicts -- counters rather than a stream, since what we need to know
    // first is whether a given preset emits OPTICS at all and how many channels.
    const OpticalSignals optical = svc.optical_signals();
    o << ",\"optics_packets\":" << optical.optics_packets
      << ",\"optics_dropped\":" << optical.optics_dropped
      << ",\"ppg_packets\":" << optical.ppg_packets
      << ",\"optics_values\":" << optical.optics_values
      << ",\"ppg_values\":" << optical.ppg_values;
    // Age, not the raw steady_clock stamp (which is process-local and means
    // nothing to a reader). Lets a consumer tell a live stream from a burst
    // that stopped. null before the first packet, distinct from "just arrived".
    const long long optical_age = svc.optical_age_ms();
    o << ",\"optics_age_ms\":";
    if (optical_age < 0) {
        o << "null";
    } else {
        o << optical_age;
    }
    o << ",\"last_optics\":";
    if (optical.optics_values > 0) {
        o << '[';
        for (int i = 0; i < optical.optics_values && i < 16; ++i) {
            if (i > 0) {
                o << ',';
            }
            o << optical.last_optics[static_cast<size_t>(i)];
        }
        o << ']';
    } else {
        o << "null";
    }
    o << ",\"last_ppg\":";
    if (optical.ppg_values > 0) {
        o << '[';
        for (int i = 0; i < optical.ppg_values && i < 3; ++i) {
            if (i > 0) {
                o << ',';
            }
            o << optical.last_ppg[static_cast<size_t>(i)];
        }
        o << ']';
    } else {
        o << "null";
    }
    // null until the headband reports anything -- "bad signal" and "no report
    // yet" are different, and only one of them justifies falling back.
    o << ",\"is_ppg_good\":";
    if (optical.has_ppg_good) {
        o << (optical.ppg_good ? "true" : "false");
    } else {
        o << "null";
    }
    o << ",\"is_heart_good\":";
    if (optical.has_heart_good) {
        o << (optical.heart_good ? "true" : "false");
    } else {
        o << "null";
    }
    const BandPowers bands = svc.band_powers();
    o << ",\"delta\":" << bands.delta
      << ",\"theta\":" << bands.theta
      << ",\"alpha\":" << bands.alpha
      << ",\"beta\":" << bands.beta
      << ",\"gamma\":" << bands.gamma;

    // Per-electrode contact quality from the headband. null when the packet
    // hasn't arrived yet, so "not reported" stays distinct from a real reading.
    const ContactQuality contact = svc.contact_quality();
    o << ",\"hsi\":";
    if (contact.has_hsi) {
        o << '[' << contact.hsi[0] << ',' << contact.hsi[1] << ','
          << contact.hsi[2] << ',' << contact.hsi[3] << ']';
    } else {
        o << "null";
    }
    o << ",\"band_channels_used\":" << svc.band_channels_used()
      << ",\"notch_filtered\":" << (svc.notch_available() ? "true" : "false");
    o << ",\"is_good\":";
    if (contact.has_is_good) {
        o << '[' << contact.is_good[0] << ',' << contact.is_good[1] << ','
          << contact.is_good[2] << ',' << contact.is_good[3] << ']';
    } else {
        o << "null";
    }

    // Additive: muse_connected keeps meaning "the link is up right now", and
    // these say whether the bridge is busy bringing it back. A consumer that
    // reads only muse_connected sees a drop; one that reads these can show
    // "reconnecting" instead of "gone" and hold off its own retry.
    const ReconnectStatus rs = svc.reconnect_status();
    o << ",\"auto_reconnect\":" << (rs.enabled ? "true" : "false")
      << ",\"reconnecting\":" << (rs.reconnecting ? "true" : "false")
      << ",\"reconnect_attempt\":" << rs.attempt
      << ",\"reconnect_max_attempts\":" << rs.max_attempts
      << ",\"reconnect_exhausted\":" << (rs.exhausted ? "true" : "false");
    // null before the first packet of a connection, and whenever not
    // connected -- "never arrived" is not "arrived 0ms ago".
    o << ",\"eeg_age_ms\":";
    if (rs.eeg_age_ms < 0) {
        o << "null";
    } else {
        o << rs.eeg_age_ms;
    }
}

void send_status_line(BridgeTcpServer& server, MuseBridgeService& svc) {
    std::ostringstream payload;
    payload << "{\"kind\":\"status\"";
    append_bridge_device_fields(payload, svc, &server);
    payload << "}";
    server.send_json_line(payload.str());
}

std::string extract_json_object_string(const std::string& s, const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    const auto kpos = s.find(needle);
    if (kpos == std::string::npos) {
        return {};
    }
    const auto colon = s.find(':', kpos + needle.size());
    if (colon == std::string::npos) {
        return {};
    }
    size_t i = colon + 1;
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) {
        ++i;
    }
    if (i >= s.size()) {
        return {};
    }
    if (s[i] == '"') {
        ++i;
        std::string out;
        while (i < s.size()) {
            if (s[i] == '\\' && i + 1 < s.size()) {
                out += s[i + 1];
                i += 2;
                continue;
            }
            if (s[i] == '"') {
                break;
            }
            out += s[i++];
        }
        return out;
    }
    const size_t start = i;
    while (i < s.size() && (std::isalnum(static_cast<unsigned char>(s[i])) || s[i] == '_' || s[i] == '-')) {
        ++i;
    }
    return s.substr(start, i - start);
}

void handle_bridge_command_line(const std::string& line, MuseBridgeService& svc, BridgeTcpServer& server) {
    const std::string cmd = extract_json_object_string(line, "cmd");
    if (cmd.empty()) {
        return;
    }
    // Every command from a person cancels the automatic recovery first: a
    // click means they are driving now, and a reconnect landing after their
    // disconnect would re-pair a headband they just released.
    if (cmd == "refresh") {
        svc.cancel_auto_reconnect();
        svc.refresh_scan();
    } else if (cmd == "disconnect") {
        svc.cancel_auto_reconnect();
        svc.disconnect_muse();
    } else if (cmd == "connect") {
        svc.cancel_auto_reconnect();
        const std::string name = extract_json_object_string(line, "name");
        if (!name.empty()) {
            const bool ok = svc.connect_named(name);
            if (!ok) {
                std::cerr << "connect failed (device not in list or LibMuse unavailable): " << name << "\n";
            }
        } else {
            std::cerr << "connect command missing \"name\"\n";
        }
    } else {
        std::cerr << "unknown bridge cmd: " << cmd << "\n";
    }
    send_status_line(server, svc);
}

void handle_signal(int) {
    g_keep_running.store(false);
}

unsigned short read_port_from_env() {
    const char* raw = std::getenv("MUSE_BRIDGE_PORT");
    if (!raw || !*raw) {
        return 8765;
    }

    char* end = nullptr;
    const long parsed = std::strtol(raw, &end, 10);
    if (end == raw || *end != '\0' || parsed <= 0 || parsed > 65535) {
        std::cerr << "Invalid MUSE_BRIDGE_PORT='" << raw << "' (expected 1-65535)\n";
        return 0;
    }

    return static_cast<unsigned short>(parsed);
}
} // namespace

int main() {
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

#if defined(ENABLE_LIBMUSE)
    // MTA lets refresh_bluetooth_state() block on Radio::GetRadiosAsync().get()
    // safely -- this process has no message pump to deadlock, unlike a UI app.
    try {
        winrt::init_apartment();
    } catch (const winrt::hresult_error& e) {
        std::cerr << "Warning: winrt::init_apartment() failed (Bluetooth radio state "
                     "checks will be unavailable): "
                  << winrt::to_string(e.message()) << "\n";
    }
#endif

    const unsigned short kPort = read_port_from_env();
    if (kPort == 0) {
        return 1;
    }

    BridgeTcpServer server;
    if (!server.start(kPort)) {
        std::cerr << "Failed to start TCP server on 127.0.0.1:" << kPort << "\n";
        std::cerr << "Common causes:\n";
        std::cerr << "- Another bridge/process is already listening on this port\n";
        std::cerr << "- You started the bridge twice\n";
        std::cerr << "\n";
        std::cerr << "Fix:\n";
        std::cerr << "- Stop the other process, or set a different port:\n";
        std::cerr << "  PowerShell: $env:MUSE_BRIDGE_PORT=8766; .\\muse_native_bridge.exe\n";
        std::cerr << "\n";
        std::cerr << "Tip: run from PowerShell (double-click closes instantly on errors)\n";
        return 1;
    }

    MuseBridgeService muse_service;
    if (!muse_service.start()) {
        std::cerr << "Failed to start Muse bridge service\n";
        return 1;
    }

    std::cout << "muse_native_bridge listening on 127.0.0.1:" << kPort << "\n";
    std::cout << "TCP commands (JSON line): {\"cmd\":\"refresh\"} | {\"cmd\":\"connect\",\"name\":\"Muse-XXXX\"} | "
                 "{\"cmd\":\"disconnect\"}\n";
    std::cout << "Press Ctrl+C to stop\n";

    using clock = std::chrono::steady_clock;
    auto last_status = clock::now();
    const auto kStatusEvery = std::chrono::milliseconds(200);

    EegFrame frame{};
    while (g_keep_running.load()) {
        std::string cmd_line;
        while (server.poll_command(cmd_line)) {
            handle_bridge_command_line(cmd_line, muse_service, server);
            last_status = clock::now();
        }

        // Before the EEG branch below, which `continue`s: while samples are
        // flowing nothing here is due, but the watchdog and a pending attempt
        // both have to be looked at on every iteration regardless.
        muse_service.service_auto_reconnect();

        // Drain optics fully, not one per loop: at 64Hz they arrive in bursts
        // between 256Hz EEG samples, and queuing them behind poll_frame's
        // 200ms wait would jitter the timestamps RMSSD is computed from.
        OpticsFrame optics{};
        while (muse_service.poll_optics(optics)) {
            std::ostringstream payload;
            // 12 significant digits, not ostringstream's default 6 -- the
            // signal lives in low-order bits that 6 digits would truncate.
            payload << std::setprecision(12);
            payload << "{\"kind\":\"optics\",\"seq\":" << optics.seq
                    << ",\"mono_ts_ms\":" << optics.mono_ts_ms
                    << ",\"n\":" << optics.n << ",\"ch\":[";
            for (int i = 0; i < optics.n; ++i) {
                if (i > 0) {
                    payload << ',';
                }
                const double v = optics.ch[static_cast<size_t>(i)];
                // nan/inf aren't valid JSON; emitting them would break parsing
                // of the whole line over one bad sample.
                if (std::isfinite(v)) {
                    payload << v;
                } else {
                    payload << "null";
                }
            }
            payload << "]}";
            // No append_bridge_device_fields here: at 64Hz that would repeat
            // every status field 64 times a second; the status line already
            // carries them at 5Hz.
            server.send_json_line(payload.str());
        }

        if (muse_service.poll_frame(frame)) {
            std::ostringstream payload;
            payload << "{\"kind\":\"eeg\",\"mono_ts_ms\":" << frame.mono_ts_ms
                    << ",\"tp9\":" << frame.tp9
                    << ",\"af7\":" << frame.af7
                    << ",\"af8\":" << frame.af8
                    << ",\"tp10\":" << frame.tp10;
            append_bridge_device_fields(payload, muse_service);
            payload << "}";
            server.send_json_line(payload.str());
            last_status = clock::now();
            continue;
        }

        if (clock::now() - last_status >= kStatusEvery) {
            send_status_line(server, muse_service);
            last_status = clock::now();
        } else {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
    }

    muse_service.stop();
    server.stop();
    return 0;
}
