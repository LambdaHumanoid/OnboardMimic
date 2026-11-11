/* Process-local fault injection: emulate a backward wall-clock step extending
 * CPython 3.8's sem_timedwait. Never change the host's clock. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <semaphore.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

int sem_timedwait(sem_t *sem, const struct timespec *deadline) {
    static __thread int (*original)(sem_t *, const struct timespec *);
    static __thread int injected;
    if (!original) original = dlsym(RTLD_NEXT, "sem_timedwait");
    char name[16] = {0};
    pthread_getname_np(pthread_self(), name, sizeof(name));
    struct timespec now;
    clock_gettime(CLOCK_REALTIME, &now);
    double remaining = deadline->tv_sec-now.tv_sec + (deadline->tv_nsec-now.tv_nsec)*1e-9;
    if (!injected && !strcmp(name, "sonic-clocktest") && remaining > 0 && remaining < .1) {
        injected = 1;
        struct timespec shifted = *deadline;
        shifted.tv_sec += 2;
        const char message[] = "Injected realtime semaphore delay\n";
        ssize_t written = write(STDERR_FILENO, message, sizeof(message)-1);
        (void)written;
        return original(sem, &shifted);
    }
    return original(sem, deadline);
}
