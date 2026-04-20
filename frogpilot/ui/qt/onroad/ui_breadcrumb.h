#pragma once

#include <chrono>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <unistd.h>
#include <utility>
#include <vector>

#include "common/swaglog.h"

// Per-frame paint instrumentation. Two mechanisms:
//   1. /dev/shm/ui_last_stage gets the current stage name + monotime_ns on every
//      stage() call. Survives SIGKILL, so the file reveals what the UI was doing
//      when the manager watchdog (5s) killed it.
//   2. Stage durations accumulate per frame. If total paint time exceeds the
//      threshold, LOGW emits a breakdown of stages >1ms, which lands in swaglog
//      and rlog (as logMessage events).
class UiBreadcrumb {
public:
  static UiBreadcrumb &instance() {
    static UiBreadcrumb inst;
    return inst;
  }

  void frame_start() {
    stages_.clear();
    current_stage_ = nullptr;
    frame_start_ns_ = now_ns();
  }

  void stage(const char *name) {
    int64_t t = now_ns();
    if (current_stage_ != nullptr) {
      stages_.emplace_back(current_stage_, t - stage_start_ns_);
    }
    current_stage_ = name;
    stage_start_ns_ = t;
    write_breadcrumb(name, t);
  }

  void frame_end(int threshold_ms) {
    int64_t t = now_ns();
    if (current_stage_ != nullptr) {
      stages_.emplace_back(current_stage_, t - stage_start_ns_);
      current_stage_ = nullptr;
    }
    int64_t total_us = (t - frame_start_ns_) / 1000;
    if (total_us > (int64_t)threshold_ms * 1000) {
      std::string breakdown;
      breakdown.reserve(256);
      for (auto &[name, ns] : stages_) {
        int64_t us = ns / 1000;
        if (us >= 1000) {
          char buf[64];
          int n = snprintf(buf, sizeof(buf), " %s=%lldus", name, (long long)us);
          if (n > 0) breakdown.append(buf, n);
        }
      }
      LOGW("slow paint: total=%lldus%s", (long long)total_us, breakdown.c_str());
    }
  }

private:
  UiBreadcrumb() {
    fd_ = open("/dev/shm/ui_last_stage", O_WRONLY | O_CREAT | O_CLOEXEC, 0644);
  }
  ~UiBreadcrumb() {
    if (fd_ >= 0) close(fd_);
  }
  UiBreadcrumb(const UiBreadcrumb &) = delete;
  UiBreadcrumb &operator=(const UiBreadcrumb &) = delete;

  static int64_t now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
  }

  void write_breadcrumb(const char *name, int64_t ns) {
    if (fd_ < 0) return;
    char buf[128];
    int n = snprintf(buf, sizeof(buf), "%s %lld\n", name, (long long)ns);
    if (n <= 0) return;
    if (n > (int)sizeof(buf)) n = sizeof(buf);
    (void)pwrite(fd_, buf, n, 0);
    (void)ftruncate(fd_, n);
  }

  int fd_ = -1;
  int64_t frame_start_ns_ = 0;
  int64_t stage_start_ns_ = 0;
  const char *current_stage_ = nullptr;
  std::vector<std::pair<const char *, int64_t>> stages_;
};
