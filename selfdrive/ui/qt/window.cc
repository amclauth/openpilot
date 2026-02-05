#include "selfdrive/ui/qt/window.h"

#include <QFontDatabase>
#include <QMouseEvent>
#include <QDebug>

#include "system/hardware/hw.h"

MainWindow::MainWindow(QWidget *parent) : QWidget(parent) {
  main_layout = new QStackedLayout(this);
  main_layout->setMargin(0);

  homeWindow = new HomeWindow(this);
  main_layout->addWidget(homeWindow);
  QObject::connect(homeWindow, &HomeWindow::openSettings, this, &MainWindow::openSettings);
  QObject::connect(homeWindow, &HomeWindow::closeSettings, this, &MainWindow::closeSettings);

  settingsWindow = new SettingsWindow(this);
  main_layout->addWidget(settingsWindow);
  QObject::connect(settingsWindow, &SettingsWindow::closeSettings, this, &MainWindow::closeSettings);
  QObject::connect(settingsWindow, &SettingsWindow::reviewTrainingGuide, [=]() {
    onboardingWindow->showTrainingGuide();
    main_layout->setCurrentWidget(onboardingWindow);
  });
  QObject::connect(settingsWindow, &SettingsWindow::showDriverView, [=] {
    homeWindow->showDriverView(true);
  });

  onboardingWindow = new OnboardingWindow(this);
  main_layout->addWidget(onboardingWindow);
  QObject::connect(onboardingWindow, &OnboardingWindow::onboardingDone, [=]() {
    main_layout->setCurrentWidget(homeWindow);
  });
  if (!onboardingWindow->completed()) {
    main_layout->setCurrentWidget(onboardingWindow);
  }

  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    if (!offroad) {
      closeSettings();
    }
  });
  QObject::connect(device(), &Device::interactiveTimeout, [=]() {
    if (main_layout->currentWidget() == settingsWindow) {
      closeSettings();
    }
  });

  // load fonts
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Black.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Bold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-ExtraBold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-ExtraLight.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Medium.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Regular.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-SemiBold.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/Inter-Thin.ttf");
  QFontDatabase::addApplicationFont("../assets/fonts/JetBrainsMono-Medium.ttf");

  // no outline to prevent the focus rectangle
  setStyleSheet(R"(
    * {
      font-family: Inter;
      outline: none;
    }
  )");
  setAttribute(Qt::WA_NoSystemBackground);
}

void MainWindow::openSettings(int index, const QString &param) {
  main_layout->setCurrentWidget(settingsWindow);
  settingsWindow->setCurrentPanel(index, param);
}

void MainWindow::closeSettings() {
  main_layout->setCurrentWidget(homeWindow);

  if (uiState()->scene.started) {
    // Map is always shown when using navigate on openpilot
    if (uiState()->scene.navigate_on_openpilot) {
      homeWindow->showMapPanel(true);
    } else {
      homeWindow->showSidebar(params.getBool("Sidebar") || frogpilotUIState()->frogpilot_toggles.value("debug_mode").toBool());
    }
  }
}

bool MainWindow::eventFilter(QObject *obj, QEvent *event) {
  FrogPilotUIState &fs = *frogpilotUIState();
  FrogPilotUIScene &frogpilot_scene = fs.frogpilot_scene;
  QJsonObject &frogpilot_toggles = fs.frogpilot_toggles;

  bool ignore = false;
  switch (event->type()) {
    case QEvent::TouchBegin:
    case QEvent::TouchUpdate:
    case QEvent::TouchEnd:
    case QEvent::MouseButtonPress:
    case QEvent::MouseMove: {
      // ignore events when device is awakened by resetInteractiveTimeout
      ignore = !device()->isAwake() || frogpilot_scene.driver_camera_timer >= UI_FREQ / 2;
      device()->resetInteractiveTimeout(frogpilot_toggles.value("screen_timeout").toInt(), frogpilot_toggles.value("screen_timeout_onroad").toInt());

      // Ghost touch detection: only on MouseButtonPress to avoid double-counting
      if (event->type() == QEvent::MouseButtonPress) {
        QMouseEvent *me = static_cast<QMouseEvent*>(event);
        QPoint pos = me->pos();

        if (!ghost_check_done) {
          boot_taps.append(pos);
          if (boot_taps.size() >= 10) {
            analyzeBootTaps();
            ghost_check_done = true;
          }
        }

        if (ghost_check_done && !blocked_coords.isEmpty() && isGhostTouch(pos)) {
          return true;
        }
      }
      break;
    }
    default:
      break;
  }
  return ignore;
}

void MainWindow::analyzeBootTaps() {
  for (int i = 0; i < boot_taps.size(); i++) {
    QList<int> cluster;
    for (int j = 0; j < boot_taps.size(); j++) {
      int dx = qAbs(boot_taps[i].x() - boot_taps[j].x());
      int dy = qAbs(boot_taps[i].y() - boot_taps[j].y());
      if (dx <= 3 && dy <= 3) {
        cluster.append(j);
      }
    }
    if (cluster.size() >= 5) {
      int sum_x = 0, sum_y = 0;
      for (int idx : cluster) {
        sum_x += boot_taps[idx].x();
        sum_y += boot_taps[idx].y();
      }
      QPoint center(sum_x / cluster.size(), sum_y / cluster.size());

      // Check if this center is already near an existing blocked coord
      bool already_blocked = false;
      for (const QPoint &bc : blocked_coords) {
        if (qAbs(bc.x() - center.x()) <= 3 && qAbs(bc.y() - center.y()) <= 3) {
          already_blocked = true;
          break;
        }
      }
      if (!already_blocked) {
        blocked_coords.append(center);
        createGhostMarker(center);
        qWarning() << "Ghost touch detected at" << center
                    << "- blocking coordinate for this boot cycle";
      }
    }
  }
}

bool MainWindow::isGhostTouch(const QPoint &pos) {
  for (const QPoint &bc : blocked_coords) {
    if (qAbs(pos.x() - bc.x()) <= 3 && qAbs(pos.y() - bc.y()) <= 3) {
      return true;
    }
  }
  return false;
}

void MainWindow::createGhostMarker(const QPoint &center) {
  QWidget *marker = new QWidget(this);
  marker->setFixedSize(7, 7);
  marker->move(center.x() - 3, center.y() - 3);
  marker->setStyleSheet("background-color: rgba(255, 0, 0, 128);");
  marker->setAttribute(Qt::WA_TransparentForMouseEvents);
  marker->show();
  marker->raise();
  ghost_markers.append(marker);
}
