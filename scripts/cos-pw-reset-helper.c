/*
 * cos-pw-reset-helper.c
 * Offline password reset for stopped VMs via libguestfs.
 * 
 * Usage: cos-pw-reset-helper <disk_path> <username>
 * 
 * Reads the password hash from stdin (never passed as argv to hide from ps).
 * Validates and edits /etc/shadow in the VM disk, updates both password hash
 * and lastchg field (field 3). Verifies the write in the same session.
 * 
 * Exit codes:
 *   0 = success
 *   1 = initialization error
 *   2 = user not found
 *   3 = hash validation failed
 *   4 = shadow read error
 *   5 = write/sync error
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <guestfs.h>

#define MAX_HASH_LEN 512
#define MAX_USERNAME_LEN 32
#define MAX_SHADOW_LEN 65536

static char hash_buf[MAX_HASH_LEN];

/*
 * Validate crypt hash format: $id$salt$hash
 * Accept $1$, $5$, $6$ (MD5, SHA-256, SHA-512)
 */
static int validate_hash(const char *hash) {
    if (!hash || hash[0] != '$') return 0;
    if (hash[1] < '1' || hash[1] > '9') return 0;
    if (hash[2] != '$') return 0;
    /* Very basic: ensure it doesn't contain newlines or other control chars */
    for (int i = 0; hash[i]; i++) {
        if ((unsigned char)hash[i] < 32) return 0;
    }
    return 1;
}

/*
 * Days since epoch (for lastchg field)
 */
static long days_since_epoch(void) {
    time_t now = time(NULL);
    return now / 86400;
}

/*
 * Free a NULL-terminated string list as returned by libguestfs functions
 * like guestfs_inspect_get_mountpoints() or guestfs_inspect_os(). The C API
 * has no guestfs_free_string_list() function (that's a binding-only
 * convenience in other languages) - each string plus the array itself must
 * be freed manually.
 */
static void free_string_list(char **list) {
    if (!list) return;
    for (char **p = list; *p != NULL; p++) {
        free(*p);
    }
    free(list);
}

/*
 * Mount all filesystems for the given inspected root, in mountpoint-length
 * order (shortest first, e.g. "/" before "/boot") so parent directories
 * exist before children are mounted on top of them.
 *
 * guestfs_mount_all() is not a real libguestfs C API function (it only
 * exists as a composite guestfish command), so this reimplements it using
 * guestfs_inspect_get_mountpoints() + guestfs_mount().
 *
 * Returns 0 on success, -1 on failure.
 */
static int mount_all_filesystems(guestfs_h *g, const char *root) {
    char **mps = guestfs_inspect_get_mountpoints(g, root);
    if (!mps) {
        return -1;
    }

    int n = 0;
    for (char **p = mps; p[0] != NULL; p += 2) n++;

    if (n == 0) {
        free_string_list(mps);
        return -1;
    }

    int *order = malloc(n * sizeof(int));
    if (!order) {
        free_string_list(mps);
        return -1;
    }
    for (int i = 0; i < n; i++) order[i] = i;

    /* Insertion sort by mountpoint string length (ascending) */
    for (int i = 1; i < n; i++) {
        int key = order[i];
        size_t key_len = strlen(mps[2 * key]);
        int j = i - 1;
        while (j >= 0 && strlen(mps[2 * order[j]]) > key_len) {
            order[j + 1] = order[j];
            j--;
        }
        order[j + 1] = key;
    }

    int rc = 0;
    for (int i = 0; i < n; i++) {
        int idx = order[i];
        const char *mountpoint = mps[2 * idx];
        const char *device = mps[2 * idx + 1];
        if (guestfs_mount(g, device, mountpoint) == -1) {
            fprintf(stderr, "failed to mount %s on %s\n", device, mountpoint);
            rc = -1;
            break;
        }
    }

    free(order);
    free_string_list(mps);
    return rc;
}

/*
 * Edit shadow: find username line, replace password hash and lastchg.
 * Returns 0 on success, 1 if user not found, -1 on parse error.
 */
static int edit_shadow(char *shadow_content, const char *username, const char *hash) {
    char *line_start = shadow_content;
    char *line_end;
    int found = 0;
    char new_shadow[MAX_SHADOW_LEN] = {0};
    size_t out_pos = 0;
    long lastchg = days_since_epoch();
    
    while (*line_start && out_pos < MAX_SHADOW_LEN - 256) {
        line_end = strchr(line_start, '\n');
        if (!line_end) line_end = line_start + strlen(line_start);
        
        size_t line_len = line_end - line_start;
        char line_copy[512];
        if (line_len >= sizeof(line_copy)) {
            fprintf(stderr, "shadow line too long\n");
            return -1;
        }
        strncpy(line_copy, line_start, line_len);
        line_copy[line_len] = '\0';
        
        char *colon = strchr(line_copy, ':');
        if (!colon) {
            fprintf(stderr, "malformed shadow line (no colon)\n");
            return -1;
        }
        
        size_t name_len = colon - line_copy;
        if (strncmp(line_copy, username, name_len) == 0 && username[name_len] == '\0') {
            /* Found the user. Parse and rebuild. */
            found = 1;
            char *field = line_copy;
            
            int wrote = snprintf(new_shadow + out_pos, MAX_SHADOW_LEN - out_pos, "%s:%s:%ld:", 
                                 username, hash, lastchg);
            if (wrote < 0 || out_pos + wrote >= MAX_SHADOW_LEN) {
                fprintf(stderr, "shadow output buffer overflow\n");
                return -1;
            }
            out_pos += wrote;
            
            /* Skip username:password:lastchg: */
            field = strchr(field, ':');
            if (!field) return -1;
            field = strchr(field + 1, ':');
            if (!field) return -1;
            field = strchr(field + 1, ':');
            if (!field) return -1;
            field++;
            
            /* Copy remaining fields (min_age:max_age:warn:inactive:expire:reserved) */
            size_t remaining = strlen(field);
            if (out_pos + remaining >= MAX_SHADOW_LEN) {
                fprintf(stderr, "shadow output buffer overflow\n");
                return -1;
            }
            strcpy(new_shadow + out_pos, field);
            out_pos += remaining;
        } else {
            /* Keep line as-is */
            if (out_pos + line_len + 1 >= MAX_SHADOW_LEN) {
                fprintf(stderr, "shadow output buffer overflow\n");
                return -1;
            }
            strncpy(new_shadow + out_pos, line_start, line_len);
            out_pos += line_len;
            new_shadow[out_pos++] = '\n';
        }
        
        if (*line_end) {
            line_start = line_end + 1;
        } else {
            break;
        }
    }
    
    if (!found) return 1;
    
    strcpy(shadow_content, new_shadow);
    return 0;
}

int main(int argc, char *argv[]) {
    guestfs_h *g;
    char *shadow_content = NULL;
    char username[MAX_USERNAME_LEN];
    size_t hash_len;
    int ret;
    
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <disk_path> <username>\n", argv[0]);
        return 1;
    }
    
    strncpy(username, argv[2], sizeof(username) - 1);
    username[sizeof(username) - 1] = '\0';
    
    /* Read hash from stdin */
    if (fgets(hash_buf, sizeof(hash_buf), stdin) == NULL) {
        fprintf(stderr, "failed to read hash from stdin\n");
        return 1;
    }
    
    /* Remove trailing newline */
    hash_len = strlen(hash_buf);
    if (hash_len > 0 && hash_buf[hash_len - 1] == '\n') {
        hash_buf[hash_len - 1] = '\0';
        hash_len--;
    }
    
    if (!validate_hash(hash_buf)) {
        fprintf(stderr, "invalid hash format\n");
        return 3;
    }
    
    /* Initialize libguestfs */
    g = guestfs_create();
    if (!g) {
        fprintf(stderr, "failed to create guestfs handle\n");
        return 1;
    }
    
    /* Add and launch */
    if (guestfs_add_drive_opts(g, argv[1], GUESTFS_ADD_DRIVE_OPTS_FORMAT, "qcow2", -1) == -1) {
        fprintf(stderr, "failed to add drive\n");
        guestfs_close(g);
        return 1;
    }
    
    if (guestfs_launch(g) == -1) {
        fprintf(stderr, "failed to launch\n");
        guestfs_close(g);
        return 1;
    }
    
    /* Inspect and mount */
    char **roots = guestfs_inspect_os(g);
    if (!roots || roots[0] == NULL) {
        fprintf(stderr, "failed to inspect OS\n");
        guestfs_close(g);
        return 1;
    }
    
    char *root = roots[0];
    
    /* Mount all filesystems */
    if (mount_all_filesystems(g, root) == -1) {
        fprintf(stderr, "failed to mount filesystems\n");
        free_string_list(roots);
        guestfs_close(g);
        return 1;
    }
    free_string_list(roots);  /* root/roots no longer needed past this point */
    
    /* Read /etc/shadow */
    shadow_content = guestfs_cat(g, "/etc/shadow");
    if (!shadow_content) {
        fprintf(stderr, "failed to read /etc/shadow\n");
        guestfs_umount_all(g);
        guestfs_close(g);
        return 4;
    }
    
    /* Edit shadow */
    ret = edit_shadow(shadow_content, username, hash_buf);
    if (ret == 1) {
        fprintf(stderr, "user '%s' not found in /etc/shadow\n", username);
        free(shadow_content);
        guestfs_umount_all(g);
        guestfs_close(g);
        return 2;
    }
    if (ret < 0) {
        fprintf(stderr, "failed to parse /etc/shadow\n");
        free(shadow_content);
        guestfs_umount_all(g);
        guestfs_close(g);
        return 4;
    }
    
    /* Write back */
    if (guestfs_write(g, "/etc/shadow", shadow_content, strlen(shadow_content)) == -1) {
        fprintf(stderr, "failed to write /etc/shadow\n");
        free(shadow_content);
        guestfs_umount_all(g);
        guestfs_close(g);
        return 5;
    }
    
    /* Verify write in same session */
    char *verify = guestfs_cat(g, "/etc/shadow");
    if (!verify || strcmp(verify, shadow_content) != 0) {
        fprintf(stderr, "verification failed: written content doesn't match\n");
        free(shadow_content);
        free(verify);
        guestfs_umount_all(g);
        guestfs_close(g);
        return 5;
    }
    free(verify);
    
    fprintf(stderr, "password reset successful for user '%s'\n", username);
    
    /* Cleanup */
    free(shadow_content);
    guestfs_sync(g);
    guestfs_umount_all(g);
    guestfs_close(g);
    
    return 0;
}
