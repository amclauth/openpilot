#pragma once

#include "omx_encoder.h"
#include "blocking_queue.h"

#include "selfdrive/ui/qt/onroad/buttons.h"

class ScreenRecorder : public QPushButton {
#ifdef NO_SR
  public:
    explicit ScreenRecorder(QWidget *parent = nullptr){}
    ~ScreenRecorder() override{}

    void updateScreen(){}
    void toggle(){}
#else
  Q_OBJECT

public:
  explicit ScreenRecorder(QWidget *parent = 0);
  ~ScreenRecorder() override;

  void updateScreen();

private:
  void closeEncoder();
  void encodingThreadFunction();
  void openEncoder(const std::string &filename);
  void paintEvent(QPaintEvent *event) override;
  void start();
  void stop();
  void toggleRecording();

  bool recording;

  int frame;
  int screenHeight;
  int screenWidth;

  long long started;

  std::unique_ptr<OmxEncoder> encoder;
  std::unique_ptr<uint8_t[]> rgbScaleBuffer;

  std::thread encodingThread;

  BlockingQueue<QImage> imageQueue{30};

  QWidget *rootWidget;

  QPixmap recorderIcon;
  QPixmap recordingIcon;
#endif //NO_SR
};
