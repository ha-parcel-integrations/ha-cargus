"""Tests for the per-request header builder."""

from custom_components.cargus.tracking import headers


def test_header_builder_takes_the_message_as_an_argument():
    built = headers.build_request_headers("491234567890")
    assert set(built) == {"k", "seed", "s", "Accept"}
    assert built["s"] == "M"


def test_builder_output_differs_per_call():
    one = headers.build_request_headers("491234567890")
    two = headers.build_request_headers("491234567890")
    assert one["k"] != two["k"]
    assert one["seed"] != two["seed"]


def test_new_message_is_twelve_characters_and_fresh():
    assert len(headers.new_message()) == 12
    assert headers.new_message() != headers.new_message()
