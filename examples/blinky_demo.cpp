// Runs the generated C++ state machine end to end and checks the EXPECTED
// trace. `tools/verify_codegen.py` compiles and runs this program.

#include <cstdio>
#include <cstring>
#include <string>

#include "Blinky.hpp"
#include "blinky_ctx.h"

namespace
{
std::string g_trace;
int         g_fail = 0;

void trace(const char* s)
{
    if (!g_trace.empty())
    {
        g_trace += ' ';
    }
    g_trace += s;
}
}  // namespace

// --- hooks the model calls (C linkage) ---------------------------------
extern "C" void led_write(bool on)
{
    if (on)
    {
        trace("L1");
    }
    else
    {
        trace("L0");
    }
}

extern "C" void fault_signal(bool on)
{
    if (on)
    {
        trace("F1");
    }
    else
    {
        trace("F0");
    }
}

extern "C" void system_halt(void)
{
    trace("H");
}

namespace
{

blinky_ctx_t   g_ctx;
blinky::Blinky g_sm(&g_ctx);

using State = blinky::Blinky::State;
using Event = blinky::Blinky::Event;

void checkState(const char* tag, State expected)
{
    const State actual = g_sm.state();

    if (actual != expected)
    {
        std::printf("FAIL  %-12s state = %s, expected = %s\n", tag,
                    blinky::Blinky::stateName(actual),
                    blinky::Blinky::stateName(expected));
        ++g_fail;
    }
}

void checkU32(const char* tag, std::uint32_t actual, std::uint32_t expected)
{
    if (actual != expected)
    {
        std::printf("FAIL  %-12s = %lu, expected = %lu\n", tag,
                    static_cast<unsigned long>(actual),
                    static_cast<unsigned long>(expected));
        ++g_fail;
    }
}

}  // namespace

int main()
{
    const char* expectedTrace = "L0 L1 L0 L1 L0 L0 L1 L0 F1 F0 L0 L1 L0 H";

    std::memset(&g_ctx, 0, sizeof(g_ctx));
    g_trace.clear();

    g_sm.start();
    checkState("start", State::Off);

    // Off -> Running -> (default entry) LedOn
    static_cast<void>(g_sm.dispatch(Event::Button));
    checkState("BUTTON", State::LedOn);
    if (!g_sm.isIn(State::Running))
    {
        std::printf("FAIL  isIn(Running) should have been true\n");
        ++g_fail;
    }

    // blinking
    static_cast<void>(g_sm.dispatch(Event::Tick));
    checkState("TICK", State::LedOff);
    static_cast<void>(g_sm.dispatch(Event::Tick));
    checkState("TICK", State::LedOn);
    checkU32("blink_count", g_ctx.blink_count, 1U);

    // internal transition: the state must not change
    static_cast<void>(g_sm.dispatch(Event::Fault));
    checkState("FAULT(int)", State::LedOn);
    checkU32("error_count", g_ctx.error_count, 1U);

    // the do activity comes from the enclosing state
    g_sm.doActivity();
    checkU32("uptime_ticks", g_ctx.uptime_ticks, 1U);

    // BUTTON -> Check -> (guard false) -> Off
    static_cast<void>(g_sm.dispatch(Event::Button));
    checkState("BUTTON->chk", State::Off);

    // four more faults: five in total, which is above the limit of three
    static_cast<void>(g_sm.dispatch(Event::Button));
    for (int i = 0; i < 4; ++i)
    {
        static_cast<void>(g_sm.dispatch(Event::Fault));
    }
    checkU32("error_count", g_ctx.error_count, 5U);

    static_cast<void>(g_sm.dispatch(Event::Button));
    checkState("BUTTON->flt", State::Fault);

    static_cast<void>(g_sm.dispatch(Event::Reset));
    checkState("RESET", State::Off);
    checkU32("error_count", g_ctx.error_count, 0U);

    // an event the active state does not handle must be ignored
    if (g_sm.dispatch(Event::Tick))
    {
        std::printf("FAIL  TICK must not be handled while in Off\n");
        ++g_fail;
    }

    // shutdown
    static_cast<void>(g_sm.dispatch(Event::Button));
    static_cast<void>(g_sm.dispatch(Event::Shutdown));
    checkState("SHUTDOWN", State::Done);
    if (!g_sm.isTerminated())
    {
        std::printf("FAIL  isTerminated() should have been true\n");
        ++g_fail;
    }

    if (g_trace != expectedTrace)
    {
        std::printf("FAIL  trace    = \"%s\"\n      expected = \"%s\"\n",
                    g_trace.c_str(), expectedTrace);
        ++g_fail;
    }

    if (g_fail == 0)
    {
        std::printf("C++: OK - every check passed (trace: %s)\n",
                    g_trace.c_str());
        return 0;
    }
    std::printf("C++: %d check(s) failed\n", g_fail);
    return 1;
}
