// Pure C++ startup probe: no ROS, network, GPU or robot dependencies.
// Cross-build with the controller's sysroot/link flags, then run under QEMU
// with that sysroot. A loader --list alone cannot detect mismatched CRT files.
#include <iostream>

static bool initialized = false;
struct StartupProbe {
    StartupProbe() { initialized = true; }
};
static StartupProbe probe;

int main() {
    if (!initialized) return 10;
    std::cout << "C++ static initialization and iostream passed\n";
    return std::cout.good() ? 0 : 11;
}
