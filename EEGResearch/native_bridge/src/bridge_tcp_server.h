#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <winsock2.h>
#include <ws2tcpip.h>

class BridgeTcpServer {
public:
    BridgeTcpServer();
    ~BridgeTcpServer();

    enum class StartResult { Started, NoToken, NetworkFailed, TokenFolderMissing, TokenWriteFailed };

    /** Listens on 127.0.0.1:port after writing a fresh token to token_path (see docs/signals.md).
     *  The token results are fully explained on stderr; NetworkFailed leaves the advice to the caller. */
    StartResult start(unsigned short port, const std::filesystem::path& token_path);
    void stop();
    void send_json_line(const std::string& payload);

    /** Non-blocking: returns true and sets line_out (without trailing newline) if a full line was received. */
    bool poll_command(std::string& line_out);

    /** Whole lines dropped on a full send buffer (a partial write closes the client instead). */
    long long dropped_lines() const noexcept { return dropped_lines_; }

    /** %LOCALAPPDATA%\AdaptiveLearning\muse_bridge_<port>.token (one per bridge); the sidecar derives the same. */
    static std::filesystem::path token_path_from_env(unsigned short port);

    /** A path as UTF-8, for logging. */
    static std::string utf8(const std::filesystem::path& path);

private:
    void try_accept_client();
    void close_client();
    bool take_line(std::string& line_out);
    bool send_raw(const std::string& line);

    // A line past this, or a client silent past the auth deadline, is closed: no command is near it.
    static constexpr size_t kMaxLine = 4096;
    static constexpr std::uint64_t kAuthDeadlineMs = 2000;

    SOCKET listen_socket_;
    SOCKET client_socket_;
    bool started_;
    std::string recv_buffer_;
    long long dropped_lines_{0};
    std::string token_;
    bool authenticated_{false};
    // One CHALLENGE per connection, so a client cannot use the bridge as an HMAC oracle at leisure.
    bool challenged_{false};
    std::uint64_t accepted_at_ms_{0};
};
