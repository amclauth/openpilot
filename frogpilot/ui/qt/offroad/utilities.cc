#include "frogpilot/ui/qt/offroad/utilities.h"

FrogPilotUtilitiesPanel::FrogPilotUtilitiesPanel(FrogPilotSettingsWindow *parent) : FrogPilotListWidget(parent), parent(parent) {
  QJsonObject shownDescriptions = QJsonDocument::fromJson(QString::fromStdString(params.get("ShownToggleDescriptions")).toUtf8()).object();
  QString className = this->metaObject()->className();

  bool forceOpenDescriptions = false;
  if (!shownDescriptions.value(className).toBool(false)) {
    forceOpenDescriptions = true;
    shownDescriptions.insert(className, true);
    params.put("ShownToggleDescriptions", QJsonDocument(shownDescriptions).toJson(QJsonDocument::Compact).toStdString());
  }

  ParamControl *debugModeToggle = new ParamControl("DebugMode", tr("Debug Mode"), tr("<b>Use FrogPilot's developer metrics on your next drive</b> to diagnose issues and improve bug reports."), "");
  if (forceOpenDescriptions) {
    debugModeToggle->showDescription();
  }
  addItem(debugModeToggle);

  ParamControl *dtcScanToggle = new ParamControl("DtcScanOnBoot", tr("DTC Scan on Boot"),
    tr("<b>Scan vehicle ECUs for diagnostic trouble codes</b> during startup, before driving. Results appear below after the next boot."), "");
  if (forceOpenDescriptions) {
    dtcScanToggle->showDescription();
  }
  QObject::connect(dtcScanToggle, &ToggleControl::toggleFlipped, [this](bool state) {
    if (!state) {
      params.remove("DtcEcuMap");
    }
  });
  addItem(dtcScanToggle);

  ButtonControl *dtcResultsButton = new ButtonControl(tr("DTC Scan Results"), tr("VIEW"),
    tr("<b>View diagnostic trouble codes</b> from the most recent boot scan."));
  {
    QFile dtcFile("/data/dtc_scan.json");
    if (dtcFile.exists() && dtcFile.open(QIODevice::ReadOnly)) {
      QJsonDocument doc = QJsonDocument::fromJson(dtcFile.readAll());
      int totalDtcs = doc.object().value("total_dtcs").toInt(0);
      if (totalDtcs > 0) {
        dtcResultsButton->setValue(QString("%1 DTCs").arg(totalDtcs));
      }
    }
  }
  QObject::connect(dtcResultsButton, &ButtonControl::clicked, [this]() {
    QFile dtcFile("/data/dtc_scan.json");
    if (!dtcFile.exists() || !dtcFile.open(QIODevice::ReadOnly)) {
      ConfirmationDialog::alert(tr("No scan results yet. Enable \"DTC Scan on Boot\" and reboot."), this);
      return;
    }

    QJsonDocument doc = QJsonDocument::fromJson(dtcFile.readAll());
    QJsonObject root = doc.object();

    int ecusDiscovered = root.value("ecus_discovered").toInt(0);
    int totalDtcs = root.value("total_dtcs").toInt(0);

    QString html = QString("<b>DTC Scan</b><br>%1 ECUs scanned, %2 DTCs found<br>")
      .arg(ecusDiscovered).arg(totalDtcs);

    QJsonObject ecus = root.value("ecus").toObject();
    for (auto it = ecus.begin(); it != ecus.end(); ++it) {
      QJsonObject ecu = it.value().toObject();
      QString ecuName = ecu.value("name").toString();
      QString addr = ecu.value("address").toString();
      QJsonArray dtcs = ecu.value("dtcs").toArray();
      QString error = ecu.value("error").toString();

      if (dtcs.isEmpty() && error.isEmpty()) {
        continue;
      }

      html += QString("<br><b>%1 (%2)</b>: ").arg(ecuName, addr);

      if (!error.isEmpty()) {
        html += error;
      } else {
        QStringList dtcList;
        for (const auto &dtcVal : dtcs) {
          QJsonObject dtc = dtcVal.toObject();
          QString code = dtc.value("code").toString();
          QJsonArray statusArr = dtc.value("status").toArray();
          QStringList statuses;
          for (const auto &s : statusArr) {
            statuses << s.toString();
          }
          QString entry = code;
          if (!statuses.isEmpty()) {
            entry += " [" + statuses.join(", ") + "]";
          }
          dtcList << entry;
        }
        html += dtcList.join(", ");
      }
    }

    if (totalDtcs == 0 && ecusDiscovered > 0) {
      html += "<br><br>No trouble codes found.";
    }

    ConfirmationDialog::rich(html, this);
  });
  if (forceOpenDescriptions) {
    dtcResultsButton->showDescription();
  }
  addItem(dtcResultsButton);

  ButtonControl *flashPandaButton = new ButtonControl(tr("Flash Panda"), tr("FLASH"), tr("<b>Reinstall the Panda firmware</b> to fix connection or reliability issues."));
  QObject::connect(flashPandaButton, &ButtonControl::clicked, [parent, flashPandaButton, this]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to flash the Panda firmware?"), tr("Flash"), this)) {
      std::thread([parent, flashPandaButton, this]() {
        parent->keepScreenOn = true;

        flashPandaButton->setEnabled(false);
        flashPandaButton->setValue(tr("Flashing..."));

        params_memory.putBool("FlashPanda", true);
        while (params_memory.getBool("FlashPanda")) {
          util::sleep_for(UI_FREQ);
        }

        flashPandaButton->setValue(tr("Flashed!"));

        util::sleep_for(2500);

        flashPandaButton->setValue(tr("Rebooting..."));

        util::sleep_for(2500);

        Hardware::reboot();
      }).detach();
    }
  });
  if (forceOpenDescriptions) {
    flashPandaButton->showDescription();
  }
  addItem(flashPandaButton);

  FrogPilotButtonsControl *forceStartedButton = new FrogPilotButtonsControl(tr("Force Drive State"), tr("<b>Manually set openpilot to be offroad or onroad.</b>"), "", {tr("OFFROAD"), tr("ONROAD"), tr("OFF")}, true);
  QObject::connect(forceStartedButton, &FrogPilotButtonsControl::buttonClicked, [this](int id) {
    if (id == 0) {
      params_memory.putBool("ForceOffroad", true);
      params_memory.putBool("ForceOnroad", false);

      updateFrogPilotToggles();
    } else if (id == 1) {
      params.put("CarParams", params.get("CarParamsPersistent"));
      params.put("FrogPilotCarParams", params.get("FrogPilotCarParamsPersistent"));

      params_memory.putBool("ForceOffroad", false);
      params_memory.putBool("ForceOnroad", true);

      updateFrogPilotToggles();
    } else if (id == 2) {
      params_memory.putBool("ForceOffroad", false);
      params_memory.putBool("ForceOnroad", false);

      updateFrogPilotToggles();
    }
  });
  forceStartedButton->setCheckedButton(2);
  if (forceOpenDescriptions) {
    forceStartedButton->showDescription();
  }
  addItem(forceStartedButton);

  ButtonControl *reportIssueButton = new ButtonControl(tr("Report a Bug or an Issue"), tr("REPORT"), tr("<b>Send a bug report</b> so we can help fix the problem!"));
  QObject::connect(reportIssueButton, &ButtonControl::clicked, [this]() {
    if (!frogpilotUIState()->frogpilot_scene.online) {
      ConfirmationDialog::alert(tr("Please connect to the internet before sending a report!"), this);
      return;
    }

    QStringList report_messages;
    QString crash_report = tr("I saw an alert that said \"openpilot crashed\"");
    if (QFile::exists("/data/error_logs/error.txt")) {
      report_messages << crash_report;
    }
    QStringList additional_issues = {
      tr("Acceleration feels harsh or jerky"),
      tr("An alert was unclear and I didn't know what it meant"),
      tr("Braking is too sudden or uncomfortable"),
      tr("I'm not sure if this is normal or a bug:"),
      tr("My screen froze or is stuck loading something"),
      tr("My steering wheel buttons aren't working"),
      tr("openpilot disengages when I don't expect it"),
      tr("openpilot doesn't react to stopped vehicles ahead"),
      tr("openpilot doesn't resume from a stop"),
      tr("openpilot feels sluggish or slow to respond"),
      tr("Steering feels twitchy or unnatural"),
      tr("The car doesn't follow curves well"),
      tr("The car isn't staying centered in its lane"),
      tr("Something else (please describe)")
    };
    report_messages.append(additional_issues);

    QMap<QString, bool> needs_extra_input;
    for (const QString &issue : report_messages) {
      if (issue.contains("confused") ||
          issue.contains("crashed") ||
          issue.contains("not sure") ||
          issue.contains("Something else")) {
        needs_extra_input[issue] = true;
      }
    }

    QString selected_issue = MultiOptionDialog::getSelection(tr("What's going on?"), report_messages, "", this);
    if (selected_issue.isEmpty()) {
      return;
    }

    if (needs_extra_input.value(selected_issue, false)) {
      QString extra_input = InputDialog::getText(tr("Please describe what's happening"), this, tr("Send Report"), false, 10, "", 300).trimmed();
      if (extra_input.isEmpty()) {
        return;
      }
      selected_issue += " — " + extra_input;
    }

    QJsonObject reportData;
    reportData["Issue"] = selected_issue;
    reportData["DiscordUser"] = InputDialog::getText(tr("What's your Discord username?"), this, tr("Send Report"), false, -1, QString::fromStdString(params.get("DiscordUsername"))).trimmed();

    params.putNonBlocking("DiscordUsername", reportData["DiscordUser"].toString().toStdString());
    params_memory.put("IssueReported", QJsonDocument(reportData).toJson(QJsonDocument::Compact).toStdString());

    ConfirmationDialog::alert(tr("Report Sent! Thanks for letting us know!"), this);
  });
  if (forceOpenDescriptions) {
    reportIssueButton->showDescription();
  }
  addItem(reportIssueButton);
  reportIssueButton->setVisible(QString::fromStdString(params.get("GitRemote")).toLower() == "https://github.com/frogai/openpilot.git");

  ButtonControl *resetTogglesButton = new ButtonControl(tr("Reset Toggles to Default"), tr("RESET"), tr("<b>Reset all toggles to their default values.</b>"));
  QObject::connect(resetTogglesButton, &ButtonControl::clicked, [parent, resetTogglesButton, this]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reset all toggles to their default values?"), tr("Reset"), this)) {
      std::thread([parent, resetTogglesButton, this]() mutable {
        parent->keepScreenOn = true;

        resetTogglesButton->setEnabled(false);
        resetTogglesButton->setValue(tr("Resetting..."));

        params.putBool("DoToggleReset", true);

        resetTogglesButton->setValue(tr("Reset!"));

        util::sleep_for(2500);

        resetTogglesButton->setValue(tr("Rebooting..."));

        util::sleep_for(2500);

        Hardware::reboot();
      }).detach();
    }
  });
  if (forceOpenDescriptions) {
    resetTogglesButton->showDescription();
  }
  addItem(resetTogglesButton);

  ButtonControl *resetTogglesButtonStock = new ButtonControl(tr("Reset Toggles to Stock openpilot"), tr("RESET"), tr("<b>Reset all toggles to match stock openpilot.</b>"));
  QObject::connect(resetTogglesButtonStock, &ButtonControl::clicked, [parent, resetTogglesButtonStock, this]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reset all toggles to match stock openpilot?"), tr("Reset"), this)) {
      std::thread([parent, resetTogglesButtonStock, this]() mutable {
        parent->keepScreenOn = true;

        resetTogglesButtonStock->setEnabled(false);
        resetTogglesButtonStock->setValue(tr("Resetting..."));

        params.putBool("DoToggleResetStock", true);

        resetTogglesButtonStock->setValue(tr("Reset!"));

        util::sleep_for(2500);

        resetTogglesButtonStock->setValue(tr("Rebooting..."));

        util::sleep_for(2500);

        Hardware::reboot();
      }).detach();
    }
  });
  if (forceOpenDescriptions) {
    resetTogglesButtonStock->showDescription();
  }
  addItem(resetTogglesButtonStock);
}
