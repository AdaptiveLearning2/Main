#include "bridge_tcp_server.h"

#include <iostream>
#include <string>

#pragma comment(lib, "Ws2_32.lib")

BridgeTcpServer::BridgeTcpServer()
    : listen_socket_(INVALID_SOCKET), client_socket_(INVALID_SOCKET), started_(false) {}

BridgeTcpServer::~BridgeTcpServer() {
    stop();
}

bool BridgeTcpServer::start(unsigned short port) {
    if (started_) {
        return true;
    }

    WSADATA wsa_data{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
        return false;
    }

    listen_socket_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listen_socket_ == INVALID_SOCKET) {
        WSACleanup();
        return false;
    }

    sockaddr_in service{};
    service.sin_family = AF_INET;
    service.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    service.sin_port = htons(port);

    if (bind(listen_socket_, reinterpret_cast<SOCKADDR*>(&service), sizeof(service)) == SOCKET_ERROR) {
        const int err = WSAGetLastError();
        std::cerr << "bind() failed on 127.0.0.1:" << port << " (WSA error " << err << ")\n";
        closesocket(listen_socket_);
        listen_socket_ = INVALID_SOCKET;
        WSACleanup();
        return false;
    }

    if (listen(listen_socket_, 1) == SOCKET_ERROR) {
        const int err = WSAGetLastError();
        std::cerr << "listen() failed on 127.0.0.1:" << port << " (WSA error " << err << ")\n";
        closesocket(listen_socket_);
        listen_socket_ = INVALID_SOCKET;
        WSACleanup();
        return false;
    }

    u_long nonblocking = 1;
    ioctlsocket(listen_socket_, FIONBIO, &nonblocking);
    started_ = true;
    return true;
}

void BridgeTcpServer::stop() {
    close_client();
    if (listen_socket_ != INVALID_SOCKET) {
        closesocket(listen_socket_);
        listen_socket_ = INVALID_SOCKET;
    }
    if (started_) {
        WSACleanup();
        started_ = false;
    }
}

void BridgeTcpServer::try_accept_client() {
    if (!started_ || client_socket_ != INVALID_SOCKET) {
        return;
    }

    client_socket_ = accept(listen_socket_, nullptr, nullptr);
    if (client_socket_ == INVALID_SOCKET) {
        return;
    }
    u_long nonblocking = 1;
    ioctlsocket(client_socket_, FIONBIO, &nonblocking);
}

void BridgeTcpServer::close_client() {
    if (client_socket_ != INVALID_SOCKET) {
        closesocket(client_socket_);
        client_socket_ = INVALID_SOCKET;
    }
    recv_buffer_.clear();
}

void BridgeTcpServer::send_json_line(const std::string& payload) {
    if (!started_) {
        return;
    }
    if (client_socket_ == INVALID_SOCKET) {
        try_accept_client();
    }
    if (client_socket_ == INVALID_SOCKET) {
        return;
    }

    const std::string body = payload + "\n";
    size_t offset = 0;
    while (offset < body.size()) {
        const int sent = send(client_socket_, body.c_str() + offset,
                              static_cast<int>(body.size() - offset), 0);
        if (sent == SOCKET_ERROR) {
            const int err = WSAGetLastError();
            if (err != WSAEWOULDBLOCK) {
                close_client();
                return;
            }
            if (offset == 0) {
                // Nothing sent yet, so we're still on a line boundary. Drop
                // the whole line and count it instead of leaving it silent.
                dropped_lines_ += 1;
                return;
            }
            // Part of the line already went out. Resuming later would splice
            // the rest onto the next line, so close the connection instead.
            close_client();
            return;
        }
        // A short send is normal for a non-blocking socket, not an error.
        offset += static_cast<size_t>(sent);
    }
}

bool BridgeTcpServer::poll_command(std::string& line_out) {
    if (!started_) {
        return false;
    }
    if (client_socket_ == INVALID_SOCKET) {
        try_accept_client();
    }
    if (client_socket_ == INVALID_SOCKET) {
        return false;
    }

    char buf[2048];
    const int n = recv(client_socket_, buf, static_cast<int>(sizeof(buf)), 0);
    if (n == SOCKET_ERROR) {
        const int err = WSAGetLastError();
        if (err == WSAEWOULDBLOCK) {
            // No new data; still return a buffered line if one is waiting.
            const auto pos = recv_buffer_.find('\n');
            if (pos == std::string::npos) {
                return false;
            }
            line_out.assign(recv_buffer_, 0, pos);
            recv_buffer_.erase(0, pos + 1);
            return true;
        }
        close_client();
        return false;
    }
    if (n == 0) {
        close_client();
        return false;
    }
    recv_buffer_.append(buf, static_cast<size_t>(n));

    const auto pos = recv_buffer_.find('\n');
    if (pos == std::string::npos) {
        return false;
    }
    line_out.assign(recv_buffer_, 0, pos);
    recv_buffer_.erase(0, pos + 1);
    return true;
}
