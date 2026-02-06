#include "frogpilot/ui/qt/widgets/upload_status.h"

#include <QDateTime>
#include <QJsonDocument>
#include <QJsonObject>
#include <QUrl>
#include <QVBoxLayout>

UploadStatusWidget::UploadStatusWidget(QWidget *parent) : QFrame(parent) {
  QVBoxLayout *layout = new QVBoxLayout(this);
  layout->setContentsMargins(50, 50, 50, 50);

  header_label = new QLabel("UPLOAD STATUS");
  header_label->setStyleSheet("font-size: 50px; font-weight: bold;");
  layout->addWidget(header_label);

  // Extract hostname from CustomUploadServer param
  host_label = new QLabel();
  host_label->setStyleSheet("font-size: 36px; color: #A0A0A0;");
  std::string server_url = params.get("CustomUploadServer");
  if (!server_url.empty()) {
    QUrl url(QString::fromStdString(server_url));
    QString host = url.host();
    if (url.port() > 0) {
      host += ":" + QString::number(url.port());
    }
    host_label->setText(host);
  }
  layout->addWidget(host_label);

  layout->addStretch(1);

  progress_label = new QLabel("Waiting for uploader...");
  progress_label->setStyleSheet("font-size: 50px;");
  layout->addWidget(progress_label);

  progress_bar = new QProgressBar();
  progress_bar->setRange(0, 1000);
  progress_bar->setValue(0);
  progress_bar->setTextVisible(false);
  progress_bar->setFixedHeight(50);
  progress_bar->setStyleSheet(R"(
    QProgressBar {
      border: none;
      background-color: #555555;
      border-radius: 8px;
    }
    QProgressBar::chunk {
      background-color: #364DEF;
      border-radius: 8px;
    }
  )");
  layout->addWidget(progress_bar);

  detail_label = new QLabel();
  detail_label->setStyleSheet("font-size: 36px; color: #A0A0A0;");
  layout->addWidget(detail_label);

  setStyleSheet(R"(
    UploadStatusWidget {
      background-color: #333333;
      border-radius: 10px;
    }
  )");
}

void UploadStatusWidget::refresh() {
  std::string raw = params.get("UploaderStatus");
  if (raw.empty()) {
    progress_label->setText("Waiting for uploader...");
    progress_bar->setValue(0);
    detail_label->setText("");
    return;
  }

  QJsonDocument doc = QJsonDocument::fromJson(QByteArray(raw.c_str(), raw.size()));
  if (doc.isNull()) {
    return;
  }

  QJsonObject obj = doc.object();
  int remaining = obj["segments_remaining"].toInt();
  int batch_size = obj["batch_size"].toInt();
  double progress = obj["progress"].toDouble();
  double drive_time = obj["drive_time"].toDouble();
  bool connected = obj["connected"].toBool();

  // Hostname color: grey (initial), green (connected), red (disconnected)
  if (obj.contains("connected")) {
    host_label->setStyleSheet(connected
      ? "font-size: 36px; color: #178643;"
      : "font-size: 36px; color: #E22C2C;");
  }

  if (remaining == 0) {
    progress_label->setText("All synced");
    progress_bar->setValue(1000);
    detail_label->setText("");
  } else {
    int uploaded = batch_size - remaining;
    QString count_text = QString::number(uploaded) + "/" +
      QString::number(batch_size) + " segments";
    progress_label->setText(count_text);
    progress_bar->setValue(static_cast<int>(progress * 1000));

    if (drive_time > 0) {
      QDateTime dt = QDateTime::fromSecsSinceEpoch(static_cast<qint64>(drive_time));
      detail_label->setText("Uploading: " + dt.toString("MMM d, h:mm AP") + " drive");
    } else {
      detail_label->setText("");
    }
  }
}
