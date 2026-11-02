#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>

static pid_t g_child = 0;
static volatile sig_atomic_t g_sig = 0;

static void on_signal(int sig){
    g_sig = sig;
    if (g_child > 0) kill(g_child, SIGKILL);
}

int main(int argc, char** argv){
    if (argc < 2){
        fprintf(stderr, "usage: mono-run <prog> [args...]\n");
        return 2;
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGALRM, &sa, NULL);

    pid_t pid = fork();
    if (pid < 0){
        fprintf(stderr, "fork failed\n");
        return 2;
    }

    if (pid == 0){
        execv(argv[1], &argv[1]);
        _exit(127);
    }

    g_child = pid;

    int status = 0;
    struct rusage ru;
    memset(&ru, 0, sizeof(ru));

    while (wait4(pid, &status, 0, &ru) < 0){
        if (errno != EINTR){
            fprintf(stderr, "wait4 failed\n");
            return 2;
        }
    }

    g_child = 0;

    double cpu = (double)ru.ru_utime.tv_sec + (double)ru.ru_utime.tv_usec / 1e6
               + (double)ru.ru_stime.tv_sec + (double)ru.ru_stime.tv_usec / 1e6;

    long us = (long)(cpu * 1000000.0 + 0.5);
    if (us < 1) us = 1;

    long maxrss = ru.ru_maxrss;
    if (maxrss < 0) maxrss = 0;

    fprintf(stderr, "@@@R %ld %ld\n", us, maxrss);

    if (g_sig){
        signal(g_sig, SIG_DFL);
        raise(g_sig);
    }

    if (WIFEXITED(status)){
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)){
        return 128 + WTERMSIG(status);
    }
    return 1;
}
