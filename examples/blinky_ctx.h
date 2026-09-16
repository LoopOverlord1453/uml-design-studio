/*
 * User context and hardware hooks for the sample model (Blinky).
 * THIS FILE IS NOT GENERATED - the application engineer writes it.
 */
#ifndef BLINKY_CTX_H
#define BLINKY_CTX_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

typedef struct
{
    uint32_t blink_count;    /* how often the LED toggled inside Running */
    uint32_t error_count;    /* number of FAULT events seen              */
    uint32_t uptime_ticks;   /* advanced by the do activity of Running   */

} blinky_ctx_t;

/* Hooks the state machine calls; you implement them. */
void led_write(bool on);
void fault_signal(bool on);
void system_halt(void);

#ifdef __cplusplus
}
#endif

#endif /* BLINKY_CTX_H */
