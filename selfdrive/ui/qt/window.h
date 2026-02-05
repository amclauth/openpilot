#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/qt/home.h"
#include "selfdrive/ui/qt/offroad/onboarding.h"
#include "selfdrive/ui/qt/offroad/settings.h"

class MainWindow : public QWidget {
  Q_OBJECT

public:
  explicit MainWindow(QWidget *parent = 0);

private:
  bool eventFilter(QObject *obj, QEvent *event) override;
  void openSettings(int index = 0, const QString &param = "");
  void closeSettings();

  // Ghost touch detection
  void analyzeBootTaps();
  bool isGhostTouch(const QPoint &pos);
  void createGhostMarker(const QPoint &center);

  QStackedLayout *main_layout;
  HomeWindow *homeWindow;
  SettingsWindow *settingsWindow;
  OnboardingWindow *onboardingWindow;

  // Ghost touch detection state
  QList<QPoint> boot_taps;
  QList<QPoint> blocked_coords;
  QList<QWidget*> ghost_markers;
  bool ghost_check_done = false;

  // FrogPilot variables
  Params params;
};
