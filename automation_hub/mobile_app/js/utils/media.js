/* 思忆集 PWA — 媒体捕获工具（麦克风/相机） */
(function () {
  'use strict';

  window.MediaCapture = {

    /** Request camera stream for photo capture */
    async getCameraStream(facingMode = 'environment') {
      try {
        return await navigator.mediaDevices.getUserMedia({
          video: { facingMode, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
      } catch (err) {
        throw new Error('无法访问相机: ' + err.message);
      }
    },

    /** Capture a photo from a video stream → Blob */
    capturePhoto(stream) {
      return new Promise((resolve, reject) => {
        try {
          const video = document.createElement('video');
          video.srcObject = stream;
          video.setAttribute('playsinline', '');
          video.setAttribute('autoplay', '');
          video.muted = true;

          video.onloadedmetadata = () => {
            video.play();
            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0);
            canvas.toBlob((blob) => {
              // Stop the stream
              stream.getTracks().forEach(t => t.stop());
              resolve(blob);
            }, 'image/jpeg', 0.85);
          };
        } catch (err) {
          stream.getTracks().forEach(t => t.stop());
          reject(new Error('拍照失败: ' + err.message));
        }
      });
    },

    /** Request microphone stream for audio recording */
    async getMicStream() {
      try {
        return await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            sampleRate: 16000,
          },
          video: false,
        });
      } catch (err) {
        throw new Error('无法访问麦克风: ' + err.message);
      }
    },

    /** Record audio from stream → return Blob when stopped */
    recordAudio(stream) {
      return new Promise((resolve, reject) => {
        try {
          const chunks = [];
          const recorder = new MediaRecorder(stream, {
            mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
              ? 'audio/webm;codecs=opus' : 'audio/webm',
          });

          recorder.ondataavailable = (e) => {
            if (e.data.size > 0) chunks.push(e.data);
          };

          recorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(chunks, { type: recorder.mimeType });
            resolve(blob);
          };

          recorder.onerror = () => {
            stream.getTracks().forEach(t => t.stop());
            reject(new Error('录音失败'));
          };

          recorder.start(100); // Collect data every 100ms
          window._activeRecorder = recorder; // Allow external stop
        } catch (err) {
          stream.getTracks().forEach(t => t.stop());
          reject(new Error('录音初始化失败: ' + err.message));
        }
      });
    },

    /** Stop active recording */
    stopRecording() {
      const recorder = window._activeRecorder;
      if (recorder && recorder.state === 'recording') {
        recorder.stop();
        window._activeRecorder = null;
      }
    },

    /** Upload blob to server via capture endpoint */
    async uploadBlob(blob, fileName, captureType) {
      const formData = new FormData();
      formData.append('file', blob, fileName);
      formData.append('capture_type', captureType);
      formData.append('source', 'phone');

      const headers = {};
      if (window.api.token) headers['Authorization'] = 'Bearer ' + window.api.token;

      try {
        const res = await fetch(window.api.baseUrl + '/mobile/capture/upload', {
          method: 'POST',
          headers,
          body: formData,
        });
        if (!res.ok) throw new Error('Upload failed');
        return await res.json();
      } catch (err) {
        throw new Error('上传失败: ' + err.message);
      }
    },
  };

})();
