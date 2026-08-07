#pragma once

#include <string>
#include <winsock2.h>
#include <ws2tcpip.h>

class BridgeTcpServer {
public:
    BridgeTcpServer();
    ~BridgeTcpServer();

    bool start(unsigned short port);
    void stop();
    void send_json_line(const std::string& payload);

    /** Non-blocking: returns true and sets line_out (without trailing newline) if a full line was received. */
    bool poll_command(std::string& line_out);

    /**
     * Lines discarded because the send buffer was full.
     *
     * Whole lines only -- a partial write closes the client instead, since
     * resuming after one would splice the remainder of a truncated line onto
     * the front of the next and corrupt every line after it.
     *
     * Surfaced because a consumer reconstructing a time base from sample index
     * cannot see a dropped line in the data: it looks like the sensor simply
     * produced fewer samples. This is the counter that says otherwise.
     */
    long long dropped_lines() const noexcept { return dropped_lines_; }

private:
    void try_accept_client();
    void close_client();

    SOCKET listen_socket_;
    SOCKET client_socket_;
    bool started_;
    std::string recv_buffer_;
    long long dropped_lines_{0};
};
