# Changelog

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
