# Changelog

## [0.1.11](https://github.com/fortytwoservices/mlz-core/compare/v0.1.10...v0.1.11) (2026-11-11)


### Features

* **contracts:** contract pull requests merge themselves once their run has checked and planned them ([#29](https://github.com/fortytwoservices/mlz-core/issues/29)) ([3b24c0c](https://github.com/fortytwoservices/mlz-core/commit/3b24c0cdaa57ccc7f4807debf848d9cbe6eff55d))

## [0.1.10](https://github.com/fortytwoservices/mlz-core/compare/v0.1.9...v0.1.10) (2026-11-11)


### Features

* **install:** mlz install sets a new customer up, step by step ([#27](https://github.com/fortytwoservices/mlz-core/issues/27)) ([45a926d](https://github.com/fortytwoservices/mlz-core/commit/45a926d84a03e1bb2cbae8d66535d9e0373a483b))

## [0.1.9](https://github.com/fortytwoservices/mlz-core/compare/v0.1.8...v0.1.9) (2026-11-11)


### Bug Fixes

* **firewall:** landing zone rules wait for vending's first contract instead of stopping the hub ([#25](https://github.com/fortytwoservices/mlz-core/issues/25)) ([1124ce9](https://github.com/fortytwoservices/mlz-core/commit/1124ce9991330bd08ce19939df3db82d628dcb83))

## [0.1.8](https://github.com/fortytwoservices/mlz-core/compare/v0.1.7...v0.1.8) (2026-11-11)


### Features

* **upgrade:** mlz upgrade opens the pull request moving a customer to another release ([#23](https://github.com/fortytwoservices/mlz-core/issues/23)) ([287b24a](https://github.com/fortytwoservices/mlz-core/commit/287b24a6bc978336a99870bf8b4f64f3b91ba709))

## [0.1.7](https://github.com/fortytwoservices/mlz-core/compare/v0.1.6...v0.1.7) (2026-11-10)


### Features

* **pipeline:** run from a customer's own GitHub organisation ([#21](https://github.com/fortytwoservices/mlz-core/issues/21)) ([4e9d9e1](https://github.com/fortytwoservices/mlz-core/commit/4e9d9e108e806f913b592f658f574d72cb5e2b9c))

## [0.1.6](https://github.com/fortytwoservices/mlz-core/compare/v0.1.5...v0.1.6) (2026-11-09)


### Features

* **vending:** the pipeline commits the IP plan to the pull request ([#17](https://github.com/fortytwoservices/mlz-core/issues/17)) ([f9a7ebe](https://github.com/fortytwoservices/mlz-core/commit/f9a7ebe6edc09182361ccfd8492e7d737aac66a0))

## [0.1.5](https://github.com/fortytwoservices/mlz-core/compare/v0.1.4...v0.1.5) (2026-11-09)


### Features

* **pipelines:** main runs queue one after another; a plan that deletes a firewall policy is refused ([#15](https://github.com/fortytwoservices/mlz-core/issues/15)) ([124d2ac](https://github.com/fortytwoservices/mlz-core/commit/124d2ac1b303f0676ab898955b1fc4fc9aa7c840))

## [0.1.4](https://github.com/fortytwoservices/mlz-core/compare/v0.1.3...v0.1.4) (2026-11-08)


### Features

* **firewall:** rules by landing zone name, and the hub's logs in the central workspace ([#12](https://github.com/fortytwoservices/mlz-core/issues/12)) ([711bdce](https://github.com/fortytwoservices/mlz-core/commit/711bdcef69d13df2e7cf258ccc31e9cd7a75e75f))
* **pipelines:** no apply and no approval for a stack whose plan changes nothing ([#13](https://github.com/fortytwoservices/mlz-core/issues/13)) ([7a7474f](https://github.com/fortytwoservices/mlz-core/commit/7a7474f94d4de2febedc649729cf58d2c85da3f7))
* **pipelines:** one reusable workflow per role, applying only the reviewed plan ([#11](https://github.com/fortytwoservices/mlz-core/issues/11)) ([d08edac](https://github.com/fortytwoservices/mlz-core/commit/d08edac669d711e62e6027a3ad331a65ba7eba68))

## [0.1.3](https://github.com/fortytwoservices/mlz-core/compare/v0.1.2...v0.1.3) (2026-11-03)


### Features

* **bootstrap:** one-time setup of a customer's pipelines ([#8](https://github.com/fortytwoservices/mlz-core/issues/8)) ([f8dfa33](https://github.com/fortytwoservices/mlz-core/commit/f8dfa3372ef1e8c64593b9900a4124f9a20b717f))


### Bug Fixes

* **bootstrap:** set the existing blob service, write role IDs as Azure stores them ([#10](https://github.com/fortytwoservices/mlz-core/issues/10)) ([765a7e7](https://github.com/fortytwoservices/mlz-core/commit/765a7e79353846f34675278879247de7ed1a5c83))

## [0.1.2](https://github.com/fortytwoservices/mlz-core/compare/v0.1.1...v0.1.2) (2026-11-02)


### Features

* **ipam:** reserved ranges, a check mode for the merge queue, and fast reads ([#7](https://github.com/fortytwoservices/mlz-core/issues/7)) ([63e7e29](https://github.com/fortytwoservices/mlz-core/commit/63e7e29bfceaa629c155d051dfc60e4144bc884e))
* platform and vending roles, the config loader and YAML contracts ([#5](https://github.com/fortytwoservices/mlz-core/issues/5)) ([8425d63](https://github.com/fortytwoservices/mlz-core/commit/8425d6300958b569362f0794cc46c0020ef636b2))

## [0.1.1](https://github.com/fortytwoservices/mlz-core/compare/v0.1.0...v0.1.1) (2026-11-02)


### Features

* **governance:** central management, platform subscription placement, contract for platform and vending ([#3](https://github.com/fortytwoservices/mlz-core/issues/3)) ([41994a3](https://github.com/fortytwoservices/mlz-core/commit/41994a3f646111121899159221077a6956f313f7))

## 0.1.0 (2026-10-30)


### Features

* **governance:** management groups and policy from customer.yaml ([#1](https://github.com/fortytwoservices/mlz-core/issues/1)) ([93696fb](https://github.com/fortytwoservices/mlz-core/commit/93696fbebfc20c77afc2779c4da6e511dfb2c2b4))
