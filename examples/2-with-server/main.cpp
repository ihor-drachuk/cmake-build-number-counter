#include <iostream>
#include "version.h"

int main() {
    std::cout << "========================================" << std::endl;
    std::cout << "  Example 2: Server-Synced Build" << std::endl;
    std::cout << "========================================" << std::endl;
    std::cout << std::endl;

    std::cout << "Version: " << APP_VERSION_STRING << std::endl;
    std::cout << "Build:   " << APP_VERSION_BUILD << std::endl;
    std::cout << std::endl;

    std::cout << "This build number was fetched from the" << std::endl;
    std::cout << "central build server (or local fallback)." << std::endl;
    std::cout << std::endl;
    std::cout << "Multiple machines can share the same" << std::endl;
    std::cout << "build counter this way!" << std::endl;
    std::cout << std::endl;

    return 0;
}
