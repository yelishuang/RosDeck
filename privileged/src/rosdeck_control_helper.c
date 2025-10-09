#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void secure_clear_env(void) {
    unsetenv("LD_PRELOAD");
    unsetenv("LD_LIBRARY_PATH");
    unsetenv("PYTHONPATH");
    unsetenv("PATH");
    setenv("PATH", "/usr/bin:/bin", 1);
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "Usage: %s [reboot|shutdown]\n", argv[0]);
        return 1;
    }

    const char *action = argv[1];
    const char *systemctl_cmd = NULL;

    if (strcmp(action, "reboot") == 0) {
        systemctl_cmd = "reboot";
    } else if (strcmp(action, "shutdown") == 0 || strcmp(action, "poweroff") == 0) {
        systemctl_cmd = "poweroff";
    } else {
        fprintf(stderr, "Unsupported action: %s\n", action);
        return 2;
    }

    secure_clear_env();

    execl("/usr/bin/systemctl", "systemctl", systemctl_cmd, NULL);

    perror("execl");
    return errno ? errno : 3;
}
