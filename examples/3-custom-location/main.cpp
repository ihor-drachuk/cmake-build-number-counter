#include <iostream>
#include "cbnc-version.h"

int main() {
    std::cout << "========================================" << std::endl;
    std::cout << "  Example 3: Custom Counter Location" << std::endl;
    std::cout << "========================================" << std::endl;
    std::cout << std::endl;

    std::cout << "Version: " << APP_VERSION_STRING << std::endl;
    std::cout << "Build:   " << APP_VERSION_BUILD << std::endl;
    std::cout << std::endl;

    std::cout << "This example stores the build counter in" << std::endl;
    std::cout << "a custom location (source directory)." << std::endl;
    std::cout << std::endl;
    std::cout << "Check 'my_build_counter.txt' in this folder!" << std::endl;
    std::cout << std::endl;

    return 0;
}
