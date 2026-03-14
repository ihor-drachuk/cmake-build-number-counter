#include <iostream>

int main() {
    std::cout << "========================================" << std::endl;
    std::cout << "  Example 4: Configure-Time Build Number" << std::endl;
    std::cout << "========================================" << std::endl;
    std::cout << std::endl;

    std::cout << "Version: " << APP_VERSION_STRING << std::endl;
    std::cout << "Build:   " << APP_VERSION_BUILD << std::endl;
    std::cout << std::endl;

    std::cout << "The build number was set at configure time!" << std::endl;
    std::cout << "It is part of PROJECT_VERSION, not just a header." << std::endl;
    std::cout << "Try running 'cmake --build build' again to see it increase." << std::endl;
    std::cout << std::endl;

    return 0;
}
