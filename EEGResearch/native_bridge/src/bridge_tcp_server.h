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
     * Count of whole lines dropped because the send buffer was full.
     * A partial write closes the client instead of resuming, since resuming
     * would splice a truncated line onto the next one. Exposed so a consumer
     * can tell a dropped line apart from the sensor just producing fewer samples.
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
