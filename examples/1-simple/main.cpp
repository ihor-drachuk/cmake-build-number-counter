#include <iostream>
#include "cbnc-version.h"

int main() {
    std::cout << "========================================" << std::endl;
    std::cout << "  Example 1: Simple Build Number" << std::endl;
    std::cout << "========================================" << std::endl;
    std::cout << std::endl;

    std::cout << "Version: " << APP_VERSION_STRING << std::endl;
    std::cout << std::endl;

    std::cout << "Components:" << std::endl;
    std::cout << "  Major: " << APP_VERSION_MAJOR << std::endl;
    std::cout << "  Minor: " << APP_VERSION_MINOR << std::endl;
    std::cout << "  Patch: " << APP_VERSION_PATCH << std::endl;
    std::cout << "  Build: " << APP_VERSION_BUILD << std::endl;
    std::cout << std::endl;

    std::cout << "The build number was automatically incremented!" << std::endl;
    std::cout << "Try running 'cmake --build build' again to see it increase." << std::endl;
    std::cout << std::endl;

    return 0;
}
