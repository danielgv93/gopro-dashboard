import AVFoundation
let asset = AVURLAsset(url: URL(fileURLWithPath: CommandLine.arguments[1]))
let sem = DispatchSemaphore(value: 0)
Task {
  let playable = try await asset.load(.isPlayable)
  let track = try await asset.loadTracks(withMediaType: .video)[0]
  let reader = try AVAssetReader(asset: asset)
  let out = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange])
  reader.add(out); reader.startReading()
  var n = 0
  while out.copyNextSampleBuffer() != nil { n += 1 }
  print("playable=\(playable) frames=\(n) status=\(reader.status == .completed ? "completed" : "failed: \(String(describing: reader.error))")")
  sem.signal()
}
sem.wait()
