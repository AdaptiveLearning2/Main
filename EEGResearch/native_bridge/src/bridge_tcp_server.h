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

private:
    void try_accept_client();
    void close_client();

    SOCKET listen_socket_;
    SOCKET client_socket_;
    bool started_;
    std::string recv_buffer_;
};
