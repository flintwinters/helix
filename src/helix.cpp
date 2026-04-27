#include <iostream>

int main(int argc, char* argv[])
{
    if(argc != 2)
    {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    std::cerr << "C++ runtime scaffold not implemented yet: " << argv[1] << '\n';
    return 0;
}
