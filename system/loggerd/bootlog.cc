#include <cassert>
#include <cstdio>
#include <string>

#include "cereal/messaging/messaging.h"
#include "common/params.h"
#include "common/swaglog.h"
#include "system/loggerd/logger.h"


static kj::Array<capnp::word> build_boot_log() {
  MessageBuilder msg;
  auto boot = msg.initEvent().initBoot();

  boot.setWallTimeNanos(nanos_since_epoch());

  std::string pstore = "/sys/fs/pstore";
  std::map<std::string, std::string> pstore_map = util::read_files_in_dir(pstore);

  int i = 0;
  auto lpstore = boot.initPstore().initEntries(pstore_map.size());
  for (auto& kv : pstore_map) {
    auto lentry = lpstore[i];
    lentry.setKey(kv.first);
    lentry.setValue(capnp::Data::Reader((const kj::byte*)kv.second.data(), kv.second.size()));
    i++;
  }

  // Gather output of commands
  std::vector<std::string> bootlog_commands = {
    "[ -x \"$(command -v journalctl)\" ] && journalctl",
  };

  if (Hardware::TICI()) {
    bootlog_commands.push_back("[ -e /dev/nvme0 ] && sudo nvme smart-log --output-format=json /dev/nvme0");
  }

  auto commands = boot.initCommands().initEntries(bootlog_commands.size());
  for (int j = 0; j < bootlog_commands.size(); j++) {
    auto lentry = commands[j];

    lentry.setKey(bootlog_commands[j]);

    const std::string result = util::check_output(bootlog_commands[j]);
    lentry.setValue(capnp::Data::Reader((const kj::byte*)result.data(), result.size()));
  }

  boot.setLaunchLog(util::read_file("/tmp/launch_log"));
  return capnp::messageToFlatArray(msg);
}

int main(int argc, char** argv) {
  const std::string id = logger_get_identifier("BootCount");
  const std::string path = Path::log_root() + "/boot/" + id;
  LOGW("bootlog to %s", path.c_str());

  bool r = util::create_directories(Path::log_root() + "/boot/", 0775);
  assert(r);

  // Create lock file (uploader skips dirs with .lock files)
  const std::string lock_path = path + ".lock";
  int lock_fd = HANDLE_EINTR(open(lock_path.c_str(), O_RDWR | O_CREAT, 0664));
  assert(lock_fd >= 0);
  close(lock_fd);

  {
    RawFile file(path.c_str());
    file.write(logger_build_init_data().asBytes());
    file.write(build_boot_log().asBytes());
  }  // RawFile destructor: fflush + fclose

  // File fully written -- remove lock
  std::remove(lock_path.c_str());

  Params().put("CurrentBootlog", id.c_str());

  return 0;
}
