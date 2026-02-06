#pragma once

#include <QFrame>
#include <QLabel>
#include <QProgressBar>

#include "common/params.h"

class UploadStatusWidget : public QFrame {
  Q_OBJECT

public:
  explicit UploadStatusWidget(QWidget *parent = nullptr);
  void refresh();

private:
  Params params;
  QLabel *header_label;
  QLabel *host_label;
  QLabel *progress_label;
  QProgressBar *progress_bar;
  QLabel *detail_label;
};
