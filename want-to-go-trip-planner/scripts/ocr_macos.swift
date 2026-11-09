#!/usr/bin/env swift

import Foundation
import ImageIO
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
    exit(1)
}

guard CommandLine.arguments.count >= 2 else {
    fail("usage: ocr_macos.swift <image-path>")
}

let imagePath = CommandLine.arguments[1]
guard FileManager.default.fileExists(atPath: imagePath) else {
    fail("cannot decode image")
}
let imageUrl = URL(fileURLWithPath: imagePath)
guard
    let imageSource = CGImageSourceCreateWithURL(imageUrl as CFURL, nil),
    let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil)
else {
    fail("cannot decode image")
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.minimumTextHeight = 0.008

do {
    let supported = try request.supportedRecognitionLanguages()
    let selected = ["zh-Hans", "en-US"].filter { supported.contains($0) }
    if !selected.isEmpty {
        request.recognitionLanguages = selected
    }
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])
} catch {
    fail("Vision OCR failed: \(error.localizedDescription)")
}

let observations = (request.results ?? []).sorted {
    let verticalDifference = abs($0.boundingBox.midY - $1.boundingBox.midY)
    if verticalDifference > 0.015 {
        return $0.boundingBox.midY > $1.boundingBox.midY
    }
    return $0.boundingBox.minX < $1.boundingBox.minX
}

for observation in observations {
    if let candidate = observation.topCandidates(1).first {
        print(candidate.string)
    }
}
