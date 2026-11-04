"""The tool never needs the network. This makes sure nothing in its process can use it."""
import socket

BLOCKED = []   # what something tried to reach (reported at the end of a run)


def internet_route():
    """The local address the computer would use to reach the internet, or None if there is no route.
    Nothing is sent: a UDP 'connect' only asks the operating system for a route, here to addresses
    reserved for documentation that are never real hosts."""
    for family, target in ((socket.AF_INET, ("192.0.2.1", 9)), (socket.AF_INET6, ("2001:db8::1", 9))):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect(target)
                return s.getsockname()[0]
        except OSError:
            continue
    return None


def block_network():
    """Make every network connection attempted from Python in this process fail, and record it.
    Local inter-process sockets (AF_UNIX) keep working. Call internet_route() first if needed."""
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    local = getattr(socket, "AF_UNIX", None)

    def record(address):
        BLOCKED.append(str(address[0] if isinstance(address, tuple) else address))

    def connect(self, address):
        if self.family == local:
            return real_connect(self, address)
        record(address)
        raise OSError("network access is blocked by redact_pdf")

    def connect_ex(self, address):
        if self.family == local:
            return real_connect_ex(self, address)
        record(address)
        return 1

    def getaddrinfo(host, *args, **kwargs):
        record(host)
        raise OSError("network access is blocked by redact_pdf")

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo
