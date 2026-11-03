// Does a C++ throw reach its catch? Prints before and after, so the last line
// shows where it stopped. Status 0 caught, 10 not caught.
#include <cstdio>

struct Guard {
    ~Guard() { std::printf("exc: destructor ran\n"); std::fflush(stdout); }
};

__attribute__((noinline)) static void thrower(int v)
{
    Guard g;
    std::printf("exc: throwing\n");
    std::fflush(stdout);
    throw v;
}

int main()
{
    std::printf("exc: start\n");
    std::fflush(stdout);
    try {
        thrower(42);
    } catch (int v) {
        std::printf("exc: caught %d\n", v);
        return v == 42 ? 0 : 10;
    }
    std::printf("exc: not caught\n");
    return 10;
}
