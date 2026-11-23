// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Base64} from "@openzeppelin/contracts/utils/Base64.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";

/// @title ReleaseRecord
/// @notice 外に出した画像の「制作記録」(テストネット用)。1 トークン = 1 枚の画像。
///         画像そのものは載せず、ファイルの SHA-256 と見た目の指紋(pHash)と、出した日時だけを記録する。
///         トークン番号は画像に埋めた見えない透かしの番号。譲渡はできない(売り買いするものではない)。
contract ReleaseRecord is ERC721, Ownable {
    struct Record {
        bytes32 contentHash; // 出したファイルの SHA-256
        uint64 phash; // 見た目の指紋(64 ビット)
        uint64 releasedAt; // 出した日時(UNIX 秒)
    }

    mapping(uint256 => Record) public records;

    error NonTransferable();
    error LengthMismatch();

    constructor(string memory name_, string memory symbol_) ERC721(name_, symbol_) Ownable(msg.sender) {}

    /// @notice 記録をまとめて発行する(持ち主は契約の持ち主)。発行済みの番号があれば全体が失敗する。
    function mintBatch(
        uint256[] calldata ids,
        bytes32[] calldata contentHashes,
        uint64[] calldata phashes,
        uint64[] calldata releasedAts
    ) external onlyOwner {
        uint256 count = ids.length;
        if (contentHashes.length != count || phashes.length != count || releasedAts.length != count) {
            revert LengthMismatch();
        }
        address to = owner();
        for (uint256 i = 0; i < count; ++i) {
            records[ids[i]] = Record(contentHashes[i], phashes[i], releasedAts[i]);
            _mint(to, ids[i]);
        }
    }

    /// @dev 発行(from が 0)だけを許し、譲渡は止める
    function _update(address to, uint256 tokenId, address auth) internal override returns (address) {
        address from = _ownerOf(tokenId);
        if (from != address(0)) {
            revert NonTransferable();
        }
        return super._update(to, tokenId, auth);
    }

    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        _requireOwned(tokenId);
        Record memory r = records[tokenId];
        bytes memory json = abi.encodePacked(
            '{"name":"Release record #',
            Strings.toHexString(tokenId, 4),
            '","description":"Testnet record of a released image (no monetary value, not transferable).",',
            '"attributes":[{"trait_type":"sha256","value":"',
            Strings.toHexString(uint256(r.contentHash), 32),
            '"},{"trait_type":"phash","value":"',
            Strings.toHexString(uint256(r.phash), 8),
            '"},{"display_type":"date","trait_type":"released","value":',
            Strings.toString(r.releasedAt),
            "}]}"
        );
        return string(abi.encodePacked("data:application/json;base64,", Base64.encode(json)));
    }
}
