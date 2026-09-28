#include "bridge_tcp_server.h"

#include <windows.h>
#include <bcrypt.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>

#pragma comment(lib, "Ws2_32.lib")
#pragma comment(lib, "bcrypt.lib")

namespace {

// 32 bytes from the OS CSPRNG, as hex; empty on failure.
std::string random_token() {
    unsigned char bytes[32];
    if (BCryptGenRandom(nullptr, bytes, sizeof(bytes), BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0) {
        return {};
    }
    std::string hex;
    char two[3];
    for (unsigned char b : bytes) {
        std::snprintf(two, sizeof(two), "%02x", b);
        hex += two;
    }
    return hex;
}

bool equal_constant_time(const std::string& a, const std::string& b) {
    if (a.size() != b.size()) {
        return false;
    }
    unsigned char diff = 0;
    for (size_t i = 0; i < a.size(); ++i) {
        diff |= static_cast<unsigned char>(a[i] ^ b[i]);
    }
    return diff == 0;
}

} // namespace

std::string BridgeTcpServer::token_path_from_env(unsigned short port) {
    // No override: the sidecar derives the same path, and one setting can't name a file per port.
    const char* base = std::getenv("LOCALAPPDATA");
    if (!base || !*base) {
        return {};
    }
    const std::string dir = std::string(base) + "\\AdaptiveLearning";
    CreateDirectoryA(dir.c_str(), nullptr);
    return dir + "\\muse_bridge_" + std::to_string(port) + ".token";
}

BridgeTcpServer::BridgeTcpServer()
    : listen_socket_(INVALID_SOCKET), client_socket_(INVALID_SOCKET), started_(false) {}

BridgeTcpServer::~BridgeTcpServer() {
    stop();
}

bool BridgeTcpServer::start(unsigned short port, const std::string& token_path) {
    if (started_) {
        return true;
    }

    token_ = random_token();
    if (token_.empty() || token_path.empty()) {
        std::cerr << "No bridge token: LOCALAPPDATA is not set\n";
        return false;
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

    // Otherwise a process binding with SO_REUSEADDR could take the port and read the sidecar's AUTH line.
    BOOL exclusive = TRUE;
    setsockopt(listen_socket_, SOL_SOCKET, SO_EXCLUSIVEADDRUSE,
               reinterpret_cast<const char*>(&exclusive), sizeof(exclusive));

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

    // Only once the port is ours: a second bridge that fails to bind must not replace the running one's token.
    {
        std::ofstream out(token_path, std::ios::trunc);
        out << token_;
        if (!out) {
            std::cerr << "Could not write the bridge token to " << token_path << "\n";
            closesocket(listen_socket_);
            listen_socket_ = INVALID_SOCKET;
            WSACleanup();
            return false;
        }
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
    authenticated_ = false;
    accepted_at_ms_ = GetTickCount64();
}

void BridgeTcpServer::close_client() {
    if (client_socket_ != INVALID_SOCKET) {
        closesocket(client_socket_);
        client_socket_ = INVALID_SOCKET;
    }
    recv_buffer_.clear();
    authenticated_ = false;
}

void BridgeTcpServer::send_json_line(const std::string& payload) {
    if (!started_) {
        return;
    }
    if (client_socket_ == INVALID_SOCKET) {
        try_accept_client();
    }
    // Nothing streams to a client that has not proved it read the token file.
    if (client_socket_ == INVALID_SOCKET || !authenticated_) {
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
                // Still on a line boundary: drop the whole line and count it.
                dropped_lines_ += 1;
                return;
            }
            // Mid-line: resuming would splice the rest onto the next line.
            close_client();
            return;
        }
        offset += static_cast<size_t>(sent);
    }
}

// The next complete line for the caller. The first line must be "AUTH <token>"; anything else,
// an HTTP request included, closes the client, as does a line or unterminated buffer past kMaxLine.
bool BridgeTcpServer::take_line(std::string& line_out) {
    for (;;) {
        const auto pos = recv_buffer_.find('\n');
        if (pos == std::string::npos) {
            if (recv_buffer_.size() > kMaxLine) {
                close_client();
            }
            return false;
        }
        if (pos > kMaxLine) {
            close_client();
            return false;
        }
        std::string line(recv_buffer_, 0, pos);
        recv_buffer_.erase(0, pos + 1);
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (!authenticated_) {
            if (!equal_constant_time(line, "AUTH " + token_)) {
                close_client();
                return false;
            }
            authenticated_ = true;
            continue;
        }
        line_out = std::move(line);
        return true;
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
    // A client holding the only slot without authenticating would lock the sidecar out.
    if (!authenticated_ && GetTickCount64() - accepted_at_ms_ > kAuthDeadlineMs) {
        close_client();
        return false;
    }

    char buf[2048];
    const int n = recv(client_socket_, buf, static_cast<int>(sizeof(buf)), 0);
    if (n == SOCKET_ERROR) {
        const int err = WSAGetLastError();
        if (err == WSAEWOULDBLOCK) {
            // No new data; still return a buffered line if one is waiting.
            return take_line(line_out);
        }
        close_client();
        return false;
    }
    if (n == 0) {
        close_client();
        return false;
    }
    recv_buffer_.append(buf, static_cast<size_t>(n));
    return take_line(line_out);
}
