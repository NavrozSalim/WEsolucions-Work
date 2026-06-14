"""Tests for Sears seller-level push lock."""
from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase

from sync.sears_seller_lock import (
    release_sears_seller_lock,
    sears_seller_lock_key,
    try_acquire_sears_seller_lock,
)


class SearsSellerLockTests(SimpleTestCase):
    @patch('sync.sears_seller_lock.cache')
    def test_acquire_and_release(self, mock_cache):
        mock_cache.add.return_value = True
        mock_cache.get.return_value = 'owner-1'

        self.assertTrue(try_acquire_sears_seller_lock('10673110', 'owner-1'))
        args, _kwargs = mock_cache.add.call_args
        self.assertEqual(args[0], sears_seller_lock_key('10673110'))
        self.assertEqual(args[1], 'owner-1')

        release_sears_seller_lock('10673110', 'owner-1')
        mock_cache.delete.assert_called_once_with(sears_seller_lock_key('10673110'))

    @patch('sync.sears_seller_lock.cache')
    def test_release_ignored_for_other_owner(self, mock_cache):
        mock_cache.get.return_value = 'other-owner'
        release_sears_seller_lock('10673110', 'owner-1')
        mock_cache.delete.assert_not_called()
