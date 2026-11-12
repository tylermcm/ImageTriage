// Real sockets, isolated loopback server, no tunnel or desktop UI.
#include "../src/win/platform_win.cpp"
#include "../src/core/http.h"
#include <iostream>
#include <stdexcept>

void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}
std::string request(int port, const std::string& method, const std::string& path,
                    const std::string& body = {}) {
    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    require(sock != INVALID_SOCKET, "client socket");
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons((u_short)port);
    if (connect(sock, (sockaddr*)&address, sizeof address)) {
        closesocket(sock);
        throw std::runtime_error("connect");
    }
    plat::tcp_configure((plat::Sock)sock, 3000);
    std::string wire = method + " " + path + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Length: " +
                       std::to_string(body.size()) + "\r\n\r\n" + body;
    require(send(sock, wire.data(), (int)wire.size(), 0) == (int)wire.size(), "send");
    std::string reply;
    char buffer[4096];
    for (int n; (n = recv(sock, buffer, sizeof buffer, 0)) > 0;) reply.append(buffer, n);
    closesocket(sock);
    return reply;
}
void status(const std::string& reply, int expected) {
    require(reply.rfind("HTTP/1.1 " + std::to_string(expected) + " ", 0) == 0, reply.c_str());
}
int main() {
    try {
        HttpServer server;
        require(server.start(0), "start loopback");
        auto reply = request(server.port(), "GET", "/" + server.token() + "/");
        status(reply, 302);
        auto begin = reply.find("Location: ");
        require(begin != std::string::npos, "session redirect");
        begin += 10;
        std::string session = reply.substr(begin, reply.find("\r\n", begin) - begin);
        status(request(server.port(), "POST", session + "auth"), 403);
        status(request(server.port(), "POST", session + "text", "blocked"), 401);
        reply = request(server.port(), "GET", session + "api/manifest");
        require(reply.find("\"receiveAuth\":true") != std::string::npos &&
                reply.find("\"receiveOk\":false") != std::string::npos, "empty code must fail closed");
        server.setReceiveCode("ABCDEFGH");
        status(request(server.port(), "POST", session + "auth", "abcd-efgh"), 204);
        status(request(server.port(), "POST", session + "text", "accepted"), 204);
        require(server.takeReceived().size() == 1, "authorized text received");
        server.setReceiveCode("");
        status(request(server.port(), "POST", session + "text", "blocked again"), 401);
        status(request(server.port(), "POST", session + "auth", "ABCDEFGH"), 403);
        server.setReceiveCode("HGFEDCBA");
        for (int i = 0; i < 8; ++i)
            status(request(server.port(), "POST", session + "auth", "wrong"), i == 7 ? 429 : 401);
        status(request(server.port(), "POST", session + "auth", "HGFEDCBA"), 429);
        server.newLink();
        status(request(server.port(), "GET", session + "api/manifest"), 404);
        server.stop();
        require(server.port() == 0, "stopped listener port must clear");
        require(!server.start(0, false, "invalid-address"), "invalid address accepted");
        require(!server.start(0, false, "0.0.0.0"), "wildcard address accepted");
        require(server.port() == 0, "failed listener port must stay clear");
        require(server.start(0, true, "invalid-address"), "loopback override");
        status(request(server.port(), "GET", "/ping"), 204);
        std::cout << "PASS: fail-closed receive authorization, code reset, lockout, session revocation, safe binding\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
