/*
 * Runs the generated C state machine end to end and checks the EXPECTED
 * trace. `tools/verify_codegen.py` compiles and runs this program.
 */
#include <stdio.h>
#include <string.h>

#include "blinky.h"
#include "blinky_ctx.h"

static char  g_trace[256];
static blinky_ctx_t g_ctx;
static blinky_t     g_sm;
static int   g_fail;

static void trace(const char *s)
{
    if ((strlen(g_trace) + strlen(s) + 2U) < sizeof(g_trace))
    {
        if (g_trace[0] != '\0')
        {
            (void)strcat(g_trace, " ");
        }
        (void)strcat(g_trace, s);
    }
}

/* --- hooks the model calls --------------------------------------------- */
void led_write(bool on)
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

void fault_signal(bool on)
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

void system_halt(void)
{
    trace("H");
}

/* --- a small checking helper ------------------------------------------- */
static void check_state(const char *tag, blinky_state_t expected)
{
    blinky_state_t actual = blinky_state(&g_sm);

    if (actual != expected)
    {
        printf("FAIL  %-12s state = %s, expected = %s\n", tag,
               blinky_state_name(actual), blinky_state_name(expected));
        g_fail++;
    }
}

static void check_u32(const char *tag, uint32_t actual, uint32_t expected)
{
    if (actual != expected)
    {
        printf("FAIL  %-12s = %lu, expected = %lu\n", tag,
               (unsigned long)actual, (unsigned long)expected);
        g_fail++;
    }
}

int main(void)
{
    const char *expected_trace = "L0 L1 L0 L1 L0 L0 L1 L0 F1 F0 L0 L1 L0 H";
    int i;

    g_trace[0] = '\0';
    memset(&g_ctx, 0, sizeof(g_ctx));

    blinky_construct(&g_sm, &g_ctx);
    blinky_start(&g_sm);
    check_state("start", BLINKY_STATE_OFF);

    /* Off -> Running -> (default entry) LedOn */
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_BUTTON);
    check_state("BUTTON", BLINKY_STATE_LED_ON);
    if (!blinky_is_in(&g_sm, BLINKY_STATE_RUNNING))
    {
        printf("FAIL  is_in(Running) must not return false\n");
        g_fail++;
    }

    /* blinking */
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_TICK);
    check_state("TICK", BLINKY_STATE_LED_OFF);
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_TICK);
    check_state("TICK", BLINKY_STATE_LED_ON);
    check_u32("blink_count", g_ctx.blink_count, 1U);

    /* internal transition: the state must not change */
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_FAULT);
    check_state("FAULT(int)", BLINKY_STATE_LED_ON);
    check_u32("error_count", g_ctx.error_count, 1U);

    /* the do activity comes from the enclosing state */
    blinky_do(&g_sm);
    check_u32("uptime_ticks", g_ctx.uptime_ticks, 1U);

    /* BUTTON -> Check -> (guard false) -> Off */
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_BUTTON);
    check_state("BUTTON->chk", BLINKY_STATE_OFF);

    /* four more faults: five in total, which is above the limit of three */
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_BUTTON);
    for (i = 0; i < 4; i++)
    {
        (void)blinky_dispatch(&g_sm, BLINKY_EVENT_FAULT);
    }
    check_u32("error_count", g_ctx.error_count, 5U);

    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_BUTTON);
    check_state("BUTTON->flt", BLINKY_STATE_FAULT);

    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_RESET);
    check_state("RESET", BLINKY_STATE_OFF);
    check_u32("error_count", g_ctx.error_count, 0U);

    /* an event the active state does not handle must be ignored */
    if (blinky_dispatch(&g_sm, BLINKY_EVENT_TICK))
    {
        printf("FAIL  TICK must not be handled while in Off\n");
        g_fail++;
    }

    /* shutdown */
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_BUTTON);
    (void)blinky_dispatch(&g_sm, BLINKY_EVENT_SHUTDOWN);
    check_state("SHUTDOWN", BLINKY_STATE_DONE);
    if (!blinky_is_terminated(&g_sm))
    {
        printf("FAIL  is_terminated() should have been true\n");
        g_fail++;
    }

    if (strcmp(g_trace, expected_trace) != 0)
    {
        printf("FAIL  trace    = \"%s\"\n      expected = \"%s\"\n",
               g_trace, expected_trace);
        g_fail++;
    }

    if (g_fail == 0)
    {
        printf("C  : OK - every check passed (trace: %s)\n", g_trace);
        return 0;
    }
    printf("C  : %d check(s) failed\n", g_fail);
    return 1;
}
