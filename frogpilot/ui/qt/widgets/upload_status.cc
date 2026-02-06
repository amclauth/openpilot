#include "frogpilot/ui/qt/widgets/upload_status.h"

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

  progress_label = new QLabel("IDLE: 0/0");
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
    return;
  }

  QJsonDocument doc = QJsonDocument::fromJson(QByteArray(raw.c_str(), raw.size()));
  if (doc.isNull()) {
    return;
  }

  QJsonObject obj = doc.object();
  int uploaded = obj["uploaded"].toInt();
  int total = obj["total"].toInt();
  double progress = obj["progress"].toDouble();
  QString state = obj["state"].toString();
  QString server_host = obj["server_host"].toString();

  // Update host label text from status JSON
  if (!server_host.isEmpty()) {
    host_label->setText(server_host);
  }

  // Determine display state and host color
  QString status_text;
  QString host_color;

  if (uploaded == total && total > 0) {
    // SYNCED takes priority regardless of state
    status_text = "SYNCED: " + QString::number(uploaded) + "/" + QString::number(total);
    host_color = "#178643";
  } else if (state == "uploading") {
    status_text = "UPLOADING: " + QString::number(uploaded) + "/" + QString::number(total);
    host_color = "#178643";
  } else if (state == "error") {
    status_text = "ERROR: " + QString::number(uploaded) + "/" + QString::number(total);
    host_color = "#E22C2C";
  } else if (state == "no_network") {
    status_text = "IDLE: " + QString::number(uploaded) + "/" + QString::number(total);
    host_color = "#A0A0A0";
  } else {
    status_text = "IDLE: " + QString::number(uploaded) + "/" + QString::number(total);
    host_color = "#A0A0A0";
  }

  progress_label->setText(status_text);
  progress_bar->setValue(static_cast<int>(progress * 1000));
  host_label->setStyleSheet("font-size: 36px; color: " + host_color + ";");
  detail_label->setText("");
}
