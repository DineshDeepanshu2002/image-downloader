Feature: Downloading images from a URL list file
  As an operator of a live system
  I want to download all images listed in a plaintext file
  So that they are stored on the local hard disk

  Scenario: All listed images are downloaded
    Given a URL file listing 3 available images
    When I run the downloader on that file
    Then all 3 images are stored on the local disk
    And the exit code is 0

  Scenario: A dead link does not stop the remaining downloads
    Given a URL file listing 2 available images and 1 dead link
    When I run the downloader on that file
    Then all 2 images are stored on the local disk
    And the exit code signals a partial failure

  Scenario: An unreadable input file aborts the run
    Given a URL file that does not exist
    When I run the downloader on that file
    Then no images are stored on the local disk
    And the exit code signals a fatal error
