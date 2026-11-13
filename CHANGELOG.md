# Changelog

## [0.7.0](https://github.com/fortytwoservices/mlz-core/compare/v0.6.1...v0.7.0) (2026-11-13)


### ⚠ BREAKING CHANGES

* **vending:** make a landing zone's subscription where billing_scope says ([#72](https://github.com/fortytwoservices/mlz-core/issues/72))

### Features

* **install:** give vending's apply identity the billing role to make subscriptions ([#74](https://github.com/fortytwoservices/mlz-core/issues/74)) ([d2b99e7](https://github.com/fortytwoservices/mlz-core/commit/d2b99e7d245ee71ff3c35e11350707291be76fcf))
* **vending:** make a landing zone's subscription where billing_scope says ([#72](https://github.com/fortytwoservices/mlz-core/issues/72)) ([68df9e9](https://github.com/fortytwoservices/mlz-core/commit/68df9e9ab55306dcba1091bb2ffbe7f519e2804b))

## [0.6.1](https://github.com/fortytwoservices/mlz-core/compare/v0.6.0...v0.6.1) (2026-11-13)


### Features

* **naming:** a Terraform naming helper for workload repos ([#69](https://github.com/fortytwoservices/mlz-core/issues/69)) ([a593c6b](https://github.com/fortytwoservices/mlz-core/commit/a593c6b9834fe783af76b6684e388ed9e20499cb))
* **vending:** workload repos get mlz-core's pipeline and starter ([#71](https://github.com/fortytwoservices/mlz-core/issues/71)) ([707a70d](https://github.com/fortytwoservices/mlz-core/commit/707a70dbd53d4dcb982f3290a97d1a6e9e9ec46c))

## [0.6.0](https://github.com/fortytwoservices/mlz-core/compare/v0.5.0...v0.6.0) (2026-11-13)


### ⚠ BREAKING CHANGES

* **vending:** a landing zone's deploy identity trusts its repo's apply environment by GitHub's immutable subject, no longer its main branch by name, so a workflow applies from an environment named apply; a repo vending makes needs github.team; and MLZ needs GitHub Enterprise: mlz install check refuses another plan (docs/bootstrap.md).

### Features

* **vending:** a workload repo's name, plan identity and state in each landing zone ([#66](https://github.com/fortytwoservices/mlz-core/issues/66)) ([371eda0](https://github.com/fortytwoservices/mlz-core/commit/371eda063b42fca2beccaf27c345efd7a6c16fd3))
* **vending:** vending makes each landing zone's workload repo ([#68](https://github.com/fortytwoservices/mlz-core/issues/68)) ([0171a87](https://github.com/fortytwoservices/mlz-core/commit/0171a87f9bc48af33b1fcf7b2b6f713d30025ae3))

## [0.5.0](https://github.com/fortytwoservices/mlz-core/compare/v0.4.0...v0.5.0) (2026-11-13)


### ⚠ BREAKING CHANGES

* **naming:** every resource the platform, governance and the bootstrap make gets a new name from the customer's naming template, which their next plans show as replacements; a root ID over four characters or with a hyphen needs naming.org in customer.yaml even without a naming section. The bootstrap's state account has prevent_destroy, so a run that would rename it stops: set names.storage_account in bootstrap.yaml to keep it (docs/naming.md).

### Features

* **naming:** the platform, governance and the bootstrap named from the customer's template ([#64](https://github.com/fortytwoservices/mlz-core/issues/64)) ([95f31a7](https://github.com/fortytwoservices/mlz-core/commit/95f31a75c90171f48e6e793840902f28113abbe7))
* **naming:** the platform's parts as apps, and rules for DCRs and GitHub network settings ([#62](https://github.com/fortytwoservices/mlz-core/issues/62)) ([05795d2](https://github.com/fortytwoservices/mlz-core/commit/05795d2ff7cdf5d0b5e901423bd2edd26dbff007))

## [0.4.0](https://github.com/fortytwoservices/mlz-core/compare/v0.3.8...v0.4.0) (2026-11-13)


### ⚠ BREAKING CHANGES

* **vending:** landing zones already deployed get new names, which their next plan shows as replacements; set names: in a landing zone file to keep a resource's current name. A workload whose folder name is not 2 to 5 lower-case letters and digits needs short_name in its _workload.yaml, and a root ID over four characters or with a hyphen needs naming.org in customer.yaml (docs/naming.md).

### Features

* **vending:** landing zones named from the customer's template ([#59](https://github.com/fortytwoservices/mlz-core/issues/59)) ([ba0422d](https://github.com/fortytwoservices/mlz-core/commit/ba0422dc774472edf1904ccfd5b9a01806a06b24))
* **vending:** names recorded once given, and mlz names --renew ([#60](https://github.com/fortytwoservices/mlz-core/issues/60)) ([ad10acb](https://github.com/fortytwoservices/mlz-core/commit/ad10acb603697d5c51812d353c09c1071648e0a0))

## [0.3.8](https://github.com/fortytwoservices/mlz-core/compare/v0.3.7...v0.3.8) (2026-11-13)


### Features

* **governance:** customer.yaml's naming section, checked by governance and published in its contract ([#57](https://github.com/fortytwoservices/mlz-core/issues/57)) ([17b8812](https://github.com/fortytwoservices/mlz-core/commit/17b8812f536c6844df57c2c12572cdb19b17be4d))

## [0.3.7](https://github.com/fortytwoservices/mlz-core/compare/v0.3.6...v0.3.7) (2026-11-13)


### Features

* **naming:** names from a customer template, checked against Azure's rules ([#55](https://github.com/fortytwoservices/mlz-core/issues/55)) ([1f1ce3d](https://github.com/fortytwoservices/mlz-core/commit/1f1ce3d006f8499fb8aaa88d7a1bb107b63066ba))

## [0.3.6](https://github.com/fortytwoservices/mlz-core/compare/v0.3.5...v0.3.6) (2026-11-13)


### Features

* **vending:** pull requests that delete a decommissioned landing zone's files ([#51](https://github.com/fortytwoservices/mlz-core/issues/51)) ([fd3ce4c](https://github.com/fortytwoservices/mlz-core/commit/fd3ce4c9b01b8a700f9c574bd95b90162abe3a46))

## [0.3.5](https://github.com/fortytwoservices/mlz-core/compare/v0.3.4...v0.3.5) (2026-11-13)


### Features

* **vending:** decommission a landing zone with decommission: true in its file ([#50](https://github.com/fortytwoservices/mlz-core/issues/50)) ([052d0d3](https://github.com/fortytwoservices/mlz-core/commit/052d0d33ebad560f77b1af8f8793e83d7ff26a42))

## [0.3.4](https://github.com/fortytwoservices/mlz-core/compare/v0.3.3...v0.3.4) (2026-11-13)


### Features

* **governance:** one run on a first deployment; the DNS policy's rights on the connectivity subscription ([#47](https://github.com/fortytwoservices/mlz-core/issues/47)) ([e21df03](https://github.com/fortytwoservices/mlz-core/commit/e21df03555093c0a982073750e4ae44f58527bdb))
* **platform:** region codes for every Azure region, from one file ([#49](https://github.com/fortytwoservices/mlz-core/issues/49)) ([a1c52e5](https://github.com/fortytwoservices/mlz-core/commit/a1c52e5cf7f8c88225e08e1de765380d4eefbee6))

## [0.3.3](https://github.com/fortytwoservices/mlz-core/compare/v0.3.2...v0.3.3) (2026-11-13)


### Features

* **install:** mlz install runners moves the pipelines onto a private GitHub-hosted runner ([#45](https://github.com/fortytwoservices/mlz-core/issues/45)) ([ff9eb7a](https://github.com/fortytwoservices/mlz-core/commit/ff9eb7ad468eff2c337f4ed002f43d1a7387cd15))
* **pipelines:** the daily check keeps one drift issue in the repo ([#46](https://github.com/fortytwoservices/mlz-core/issues/46)) ([1197856](https://github.com/fortytwoservices/mlz-core/commit/1197856a2d51a2e253765c260b8e834532e81228))

## [0.3.2](https://github.com/fortytwoservices/mlz-core/compare/v0.3.1...v0.3.2) (2026-11-13)


### Features

* **platform:** GitHub's network settings in management's network, and the state's private endpoint ([#43](https://github.com/fortytwoservices/mlz-core/issues/43)) ([0f59fdc](https://github.com/fortytwoservices/mlz-core/commit/0f59fdc7116cc06d0dcb6c0d9658be35bf184c1f))

## [0.3.1](https://github.com/fortytwoservices/mlz-core/compare/v0.3.0...v0.3.1) (2026-11-12)


### Features

* **platform:** firewall rules for management, identity and the private runners ([#41](https://github.com/fortytwoservices/mlz-core/issues/41)) ([651eb09](https://github.com/fortytwoservices/mlz-core/commit/651eb097601d83ad4556e52b22dc5da6f778135e))

## [0.3.0](https://github.com/fortytwoservices/mlz-core/compare/v0.2.2...v0.3.0) (2026-11-12)


### ⚠ BREAKING CHANGES

* **platform:** hubs/<region>.yaml takes platform_address_space, a /21, in place of address_space. The check refuses address_space and says what to write: a hub at 10.100.0.0/22 becomes platform_address_space: 10.100.0.0/21, which keeps the hub where it is. Upgrade vending before the platform.

### Features

* **platform:** build management's and identity's networks ([#39](https://github.com/fortytwoservices/mlz-core/issues/39)) ([56ca0d3](https://github.com/fortytwoservices/mlz-core/commit/56ca0d351b10a03774004ae32838abc458266f47))
* **platform:** platform_address_space holds the hub, management and identity ([#37](https://github.com/fortytwoservices/mlz-core/issues/37)) ([8d365ec](https://github.com/fortytwoservices/mlz-core/commit/8d365ec2a4e56dc091a4b2bc32ce5789592efba1))

## [0.2.2](https://github.com/fortytwoservices/mlz-core/compare/v0.2.1...v0.2.2) (2026-11-12)


### Features

* **new:** mlz new landing-zone starts a landing zone's files ([#35](https://github.com/fortytwoservices/mlz-core/issues/35)) ([f4704df](https://github.com/fortytwoservices/mlz-core/commit/f4704df5560c5196aa3ca2421ef3c879ccf1067f))

## [0.2.1](https://github.com/fortytwoservices/mlz-core/compare/v0.2.0...v0.2.1) (2026-11-11)


### Features

* **explain:** mlz explain shows where each of a landing zone's values comes from ([#33](https://github.com/fortytwoservices/mlz-core/issues/33)) ([4f5817e](https://github.com/fortytwoservices/mlz-core/commit/4f5817e1c2eb47b4f4fc534e8b37f810cd60def6))

## [0.2.0](https://github.com/fortytwoservices/mlz-core/compare/v0.1.11...v0.2.0) (2026-11-11)


### ⚠ BREAKING CHANGES

* **schemas:** governance refuses a top-level key in customer.yaml that is neither one of its settings nor in governance/defaults.yaml, and mlz install refuses an unknown key in bootstrap.yaml. Before upgrading, remove or correct any such key: the governance check names each one.

### Features

* **schemas:** editor help for customer YAML, and example settings in the starters ([#31](https://github.com/fortytwoservices/mlz-core/issues/31)) ([bb37fa4](https://github.com/fortytwoservices/mlz-core/commit/bb37fa48fe827803debe5519557eedb35418e9b1))

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
