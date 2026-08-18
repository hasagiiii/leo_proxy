# Cookie import fixtures

Parser tests build ZIP archives in memory from synthetic Cookie values. Real browser exports,
session Cookies, Tokens, passwords, and production account identifiers stay outside the repository.

The synthetic fixture shape mirrors the accepted Chrome export contract: a root object containing
`url` and `cookies`, with each Cookie containing `domain`, `expirationDate`, `hostOnly`, `httpOnly`,
`name`, `path`, `sameSite`, `secure`, `session`, `storeId`, and `value`.
