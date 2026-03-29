class Amaryllis < Formula
  desc "Local-first agent runtime and desktop AI workspace"
  homepage "https://github.com/amaryllis-labs/amaryllis"
  version "{{VERSION}}"

  if Hardware::CPU.arm?
    url "{{MACOS_ARM64_URL}}"
    sha256 "{{MACOS_ARM64_SHA256}}"
  else
    url "{{MACOS_X64_URL}}"
    sha256 "{{MACOS_X64_SHA256}}"
  end

  def install
    bin.install "amaryllis-runtime"
  end

  test do
    output = shell_output("#{bin}/amaryllis-runtime --help")
    assert_match "usage", output.downcase
  end
end
