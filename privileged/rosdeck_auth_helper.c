#include <security/pam_appl.h>
#include <security/pam_misc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int pam_conversation(int num_msg,
                            const struct pam_message **msg,
                            struct pam_response **resp,
                            void *appdata_ptr) {
    if (num_msg <= 0 || msg == NULL || resp == NULL) {
        return PAM_CONV_ERR;
    }

    const char *password = (const char *)appdata_ptr;
    struct pam_response *answers = calloc((size_t)num_msg, sizeof(struct pam_response));
    if (answers == NULL) {
        return PAM_BUF_ERR;
    }

    for (int i = 0; i < num_msg; ++i) {
        switch (msg[i]->msg_style) {
            case PAM_PROMPT_ECHO_OFF:
            case PAM_PROMPT_ECHO_ON:
                answers[i].resp = password ? strdup(password) : NULL;
                answers[i].resp_retcode = 0;
                break;
            case PAM_TEXT_INFO:
            case PAM_ERROR_MSG:
                answers[i].resp = NULL;
                answers[i].resp_retcode = 0;
                break;
            default:
                free(answers);
                return PAM_CONV_ERR;
        }
    }

    *resp = answers;
    return PAM_SUCCESS;
}

static void secure_zero(char *buf, size_t len) {
    if (!buf) {
        return;
    }
#if defined(__STDC_LIB_EXT1__)
    memset_s(buf, len, 0, len);
#else
    volatile unsigned char *p = (volatile unsigned char *)buf;
    while (len--) {
        *p++ = 0;
    }
#endif
}

static char *read_password_from_stdin(void) {
    size_t capacity = 256;
    size_t length = 0;
    char *buffer = malloc(capacity);
    if (!buffer) {
        return NULL;
    }

    int c;
    while ((c = fgetc(stdin)) != EOF) {
        if (c == '\n') {
            break;
        }
        if (length + 1 >= capacity) {
            size_t new_capacity = capacity * 2;
            char *new_buffer = realloc(buffer, new_capacity);
            if (!new_buffer) {
                secure_zero(buffer, length);
                free(buffer);
                return NULL;
            }
            buffer = new_buffer;
            capacity = new_capacity;
        }
        buffer[length++] = (char)c;
    }
    buffer[length] = '\0';
    return buffer;
}

int main(int argc, char *argv[]) {
    const char *username = "root";

    unsetenv("LD_PRELOAD");
    unsetenv("LD_LIBRARY_PATH");
    unsetenv("PYTHONPATH");

    for (int i = 1; i < argc; ++i) {
        if ((strcmp(argv[i], "--user") == 0 || strcmp(argv[i], "-u") == 0) && i + 1 < argc) {
            username = argv[++i];
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            fprintf(stderr, "Usage: %s [--user USERNAME]\n", argv[0]);
            return 1;
        }
    }

    char *password = read_password_from_stdin();
    if (!password) {
        fprintf(stderr, "Failed to read password from stdin\n");
        return 2;
    }

    struct pam_conv conv = {
        .conv = pam_conversation,
        .appdata_ptr = password
    };

    pam_handle_t *pamh = NULL;
    int rc = pam_start("login", username, &conv, &pamh);
    if (rc != PAM_SUCCESS) {
        secure_zero(password, strlen(password));
        free(password);
        return 3;
    }

    rc = pam_authenticate(pamh, PAM_SILENT);
    if (rc == PAM_SUCCESS) {
        rc = pam_acct_mgmt(pamh, PAM_SILENT);
    }

    pam_end(pamh, rc);

    secure_zero(password, strlen(password));
    free(password);

    if (rc == PAM_SUCCESS) {
        return 0;
    }

    return 4;
}
