# Changelog

## [0.16.2](https://github.com/fortytwoservices/mlz-core/compare/v0.16.1...v0.16.2) (2026-11-24)


### Bug Fixes

* publish checks the branch and sends its move again when GitHub's answer is lost ([#152](https://github.com/fortytwoservices/mlz-core/issues/152)) ([031823f](https://github.com/fortytwoservices/mlz-core/commit/031823f595257ddabea819864053f836a3100423))

## [0.16.1](https://github.com/fortytwoservices/mlz-core/compare/v0.16.0...v0.16.1) (2026-11-23)


### Bug Fixes

* **governance:** say that the top group's name and place change only through mlz install bootstrap ([c38653d](https://github.com/fortytwoservices/mlz-core/commit/c38653de77c811120007f963b0f1c9fd4f44bee8))

## [0.16.0](https://github.com/fortytwoservices/mlz-core/compare/v0.15.0...v0.16.0) (2026-11-23)


### ⚠ BREAKING CHANGES

* **governance:** the governance repo's settings file is governance.yaml, not customer.yaml. An existing deployment renames it in its governance repo on the pull request that upgrades it to this release (git mv customer.yaml governance.yaml), since this release refuses customer.yaml and the one before reads only that name, and in the folder mlz install reads (--settings) before running the installer from this release. mlz governance takes --file instead of --customer. Nothing in Azure changes.

### Features

* **governance:** governance.yaml, not customer.yaml, for governance's settings ([#148](https://github.com/fortytwoservices/mlz-core/issues/148)) ([944f237](https://github.com/fortytwoservices/mlz-core/commit/944f2372fed7057ac929091011b050300b8f793e))

## [0.15.0](https://github.com/fortytwoservices/mlz-core/compare/v0.14.0...v0.15.0) (2026-11-23)


### ⚠ BREAKING CHANGES

* **vending:** a landing zone's region is region, not location, as every other region setting is; location is refused with the new name. An existing deployment renames location: to region: in its vending repo (defaults/landing-zone-defaults.yaml, and any workload-defaults.yaml or landing zone file that sets it), on the pull request that upgrades it to this release, since this release refuses location and the one before refused region; nothing in Azure changes. In a workload repo var.mlz.location becomes var.mlz.region (the region's name, norwayeast) and var.mlz.region becomes var.mlz.region_code (its code, nwe). Vending writes the new values at once, so a workload repo's plan fails until its team merges the upgrade pull request vending offers, which moves variables.tf; the team also changes its own Terraform that reads those keys, and moves any pin of mlz-core's naming module to this release (an older one stops at its check). A name a team built from var.mlz.region gets the region's full name after the upgrade, and Terraform plans to replace that resource: build it from var.mlz.region_code.

### Features

* **vending:** region, not location, in a landing zone's settings ([#146](https://github.com/fortytwoservices/mlz-core/issues/146)) ([caa5803](https://github.com/fortytwoservices/mlz-core/commit/caa5803ea4d67539ae57d4d704cff52213df7545))

## [0.14.0](https://github.com/fortytwoservices/mlz-core/compare/v0.13.0...v0.14.0) (2026-11-23)


### ⚠ BREAKING CHANGES

* **install:** bootstrap.yaml needs github.app, the MLZ App's slug. An existing deployment adds that line and runs mlz install repos again, as a member of the bypass team, to add the ruleset generated-files to the three repos; then mlz install check. From then on only the MLZ App and the bypass team may push under _generated/.

### Features

* **install:** GitHub Team as a setting, checked against the organization ([#139](https://github.com/fortytwoservices/mlz-core/issues/139)) ([84c1832](https://github.com/fortytwoservices/mlz-core/commit/84c1832999486be4fdffb814090de179dde277e9))
* **install:** only the MLZ App and the bypass team change _generated/ ([#136](https://github.com/fortytwoservices/mlz-core/issues/136)) ([292d5e9](https://github.com/fortytwoservices/mlz-core/commit/292d5e93cb010e17051359b5fb699ea5bf835b1b))
* **install:** the role repos' pipeline, code owners, checks and environment per GitHub plan ([#142](https://github.com/fortytwoservices/mlz-core/issues/142)) ([3f471d1](https://github.com/fortytwoservices/mlz-core/commit/3f471d11e607576cfb51a0645ce0cf3bf51645b0))
* **pipeline:** an approval input for GitHub Team, the plans' stamp and the planned check ([#138](https://github.com/fortytwoservices/mlz-core/issues/138)) ([ac7be8b](https://github.com/fortytwoservices/mlz-core/commit/ac7be8b1fdbed9b6e4e59796ce792c0729cacb64))
* **pipeline:** the gate on GitHub Team: the approval covers the plan, and main applies only that plan ([#140](https://github.com/fortytwoservices/mlz-core/issues/140)) ([a606b26](https://github.com/fortytwoservices/mlz-core/commit/a606b26ed7982a905300f988dae48ef4422fc6a6))
* **vending:** the workload repos' pipeline, rules and apply environment per GitHub plan ([#143](https://github.com/fortytwoservices/mlz-core/issues/143)) ([68659cd](https://github.com/fortytwoservices/mlz-core/commit/68659cddff55ade0a71984b39334998e890be11d))

## [0.13.0](https://github.com/fortytwoservices/mlz-core/compare/v0.12.0...v0.13.0) (2026-11-19)


### ⚠ BREAKING CHANGES

* **install:** mlz install check fails for a role repo made before this release until its ruleset main dismisses stale approvals. Tick "Dismiss stale pull request approvals when new commits are pushed" in each role repo's ruleset main (Settings, Rules, Rulesets).
* **vending:** a workload team can no longer push to main. A change needs a pull request that another member of the landing zone's github.team approves, with workload / plan passed on a branch up to date with main; a team of one needs a second member, or the bypass team. Tell each workload team before the upgrade, and update any open pull request ("Update branch") after it.

### Features

* **install:** the role repos' main dismisses stale approvals, as the workload repos' does ([#133](https://github.com/fortytwoservices/mlz-core/issues/133)) ([4564b00](https://github.com/fortytwoservices/mlz-core/commit/4564b00e2844ed78e39840274eb8da32cef95451))
* JetBrains IDEs read the customer YAML's schemas too ([#127](https://github.com/fortytwoservices/mlz-core/issues/127)) ([c0d4cc2](https://github.com/fortytwoservices/mlz-core/commit/c0d4cc2f5381368eeb597b828a262fa005187b25))
* **pipeline:** a run that waits for approval says why, on its page and on the person's merged pull request ([#126](https://github.com/fortytwoservices/mlz-core/issues/126)) ([eafd6fb](https://github.com/fortytwoservices/mlz-core/commit/eafd6fbb1ac08bff18ee5748bf71e17f80909a65))
* **vending:** each workload repo's main takes the role repos' rule ([#132](https://github.com/fortytwoservices/mlz-core/issues/132)) ([0f891b8](https://github.com/fortytwoservices/mlz-core/commit/0f891b809b42ae915d307dd5bace9f6f65f44ef3))
* **vending:** workload repos are offered a pull request to vending's release ([#131](https://github.com/fortytwoservices/mlz-core/issues/131)) ([b27fef5](https://github.com/fortytwoservices/mlz-core/commit/b27fef591866301bcb75b4559bba3e59727aaa12))


### Bug Fixes

* **pipeline:** changes from the plan's JSON alone, never Terraform's exit code; imports, moves and forgets applied ([#130](https://github.com/fortytwoservices/mlz-core/issues/130)) ([e87f8e8](https://github.com/fortytwoservices/mlz-core/commit/e87f8e81950e9a4a58eafa66283539145c7f9526))
* **pipeline:** each script's lines reach the log before its subprocesses' output ([#129](https://github.com/fortytwoservices/mlz-core/issues/129)) ([e8d75f6](https://github.com/fortytwoservices/mlz-core/commit/e8d75f6b9121261cd1dc83b060f2764c9579ef3b))
* the bootstrap's plan read from its JSON, as the pipelines', and stale commands in the docs and messages ([#134](https://github.com/fortytwoservices/mlz-core/issues/134)) ([923cf16](https://github.com/fortytwoservices/mlz-core/commit/923cf16f6d77ccdeb398ec1cf2ce77da3c4ac34a))

## [0.12.0](https://github.com/fortytwoservices/mlz-core/compare/v0.11.5...v0.12.0) (2026-11-19)


### ⚠ BREAKING CHANGES

* .github/CODEOWNERS may no longer hand a folder to another team. Every run's check fails on any rule but the installer's two (* @<org>/<platform team> and /_generated/), naming the rule. Remove such lines; since the push ruleset, a member of the bypass team makes that change. mlz owners is gone.
* bootstrap.yaml needs github.bypass_team, the slug of a small, visible team of the organization that is not the platform team. Only its members may change .github/ in the three repos, so whoever runs mlz upgrade or changes CODEOWNERS must be in it. mlz install repos and check refuse a bootstrap.yaml without it. Make the team, set github.bypass_team, then run mlz install repos (it adds the push ruleset pipeline-files) and mlz install check, and delete branches you do not know.

### Features

* changes under .github/ only from the bypass team (finding 17) ([#120](https://github.com/fortytwoservices/mlz-core/issues/120)) ([6ae286c](https://github.com/fortytwoservices/mlz-core/commit/6ae286c8704f6f5a57b6b2c49306e98e63bc6765))
* **naming:** without {purpose} in a template, mlz-core numbers its own resources of a type in {nn} ([#125](https://github.com/fortytwoservices/mlz-core/issues/125)) ([b2fd6bb](https://github.com/fortytwoservices/mlz-core/commit/b2fd6bb9ee23e69c1609883220b6b8bc09710cb0))


### Bug Fixes

* **vending:** a new landing zone's state account without a purpose ([#124](https://github.com/fortytwoservices/mlz-core/issues/124)) ([9f231a7](https://github.com/fortytwoservices/mlz-core/commit/9f231a7de1ce29f3fd50cb4d4f5cab653295d111))


### Code Refactoring

* code owners without the handover ([#123](https://github.com/fortytwoservices/mlz-core/issues/123)) ([f64208d](https://github.com/fortytwoservices/mlz-core/commit/f64208dc6195450577f94285a94d10237e0f2024))

## [0.11.5](https://github.com/fortytwoservices/mlz-core/compare/v0.11.4...v0.11.5) (2026-11-19)


### Features

* ./mlz in each repo, one loader and one check ([#116](https://github.com/fortytwoservices/mlz-core/issues/116)) ([bcb6ab8](https://github.com/fortytwoservices/mlz-core/commit/bcb6ab8cf63cfe5d021815cd48532477c05bd825))


### Bug Fixes

* **pipeline:** read GitHub's meta with the job's token, and never retry a used-up rate limit ([#118](https://github.com/fortytwoservices/mlz-core/issues/118)) ([f6d994c](https://github.com/fortytwoservices/mlz-core/commit/f6d994c5478902d5a29e2e95f397479fe13a805c))
* **pipeline:** run mlz isolated from the customer's checkout, on every Python ([#119](https://github.com/fortytwoservices/mlz-core/issues/119)) ([d912b8e](https://github.com/fortytwoservices/mlz-core/commit/d912b8e26340efc8d5c542236ed63add6be4dcda))

## [0.11.4](https://github.com/fortytwoservices/mlz-core/compare/v0.11.3...v0.11.4) (2026-11-19)


### Bug Fixes

* **platform:** a hub with Bastion or its DNS resolver off plans again ([#113](https://github.com/fortytwoservices/mlz-core/issues/113)) ([c4be5e2](https://github.com/fortytwoservices/mlz-core/commit/c4be5e2988ba25c0ac5642d7b1d15f2952b17ad4))

## [0.11.3](https://github.com/fortytwoservices/mlz-core/compare/v0.11.2...v0.11.3) (2026-11-19)


### Features

* **explain:** explain a hub, management, identity, the rules and governance; --inputs for any stack ([#104](https://github.com/fortytwoservices/mlz-core/issues/104)) ([1d1b888](https://github.com/fortytwoservices/mlz-core/commit/1d1b8883e02eb9037f5053570b0a12563d7ec82f))
* **governance:** advanced: in customer.yaml sets governance module inputs mlz-core leaves alone ([#110](https://github.com/fortytwoservices/mlz-core/issues/110)) ([86fee51](https://github.com/fortytwoservices/mlz-core/commit/86fee513f1b4a038c6a5a092fa182c7ec13c2a67))
* **pipeline:** a plan comment starts with what changed in the stack's settings ([#106](https://github.com/fortytwoservices/mlz-core/issues/106)) ([f16a888](https://github.com/fortytwoservices/mlz-core/commit/f16a888dfc8a2d7cb4aa2bcb88c2b5cb13371539))
* **platform:** advanced: in a hub file sets hub module inputs mlz-core leaves alone ([#108](https://github.com/fortytwoservices/mlz-core/issues/108)) ([5a09bf6](https://github.com/fortytwoservices/mlz-core/commit/5a09bf6ba6f5fde4c06fc18c177c04fa732df649))
* **vending:** advanced: in a landing zone sets sub-vending inputs mlz-core leaves alone ([#109](https://github.com/fortytwoservices/mlz-core/issues/109)) ([3711605](https://github.com/fortytwoservices/mlz-core/commit/371160599fa796df3cd22872d452726d2d434a70))


### Bug Fixes

* **pipeline:** the first hub's plan comment says its stack is new ([#107](https://github.com/fortytwoservices/mlz-core/issues/107)) ([2113653](https://github.com/fortytwoservices/mlz-core/commit/211365321e5b3e289ce941dc79fd7e4910bfa880))
* **reference:** refuse an empty module section in an input map ([#103](https://github.com/fortytwoservices/mlz-core/issues/103)) ([f9d657e](https://github.com/fortytwoservices/mlz-core/commit/f9d657e2d5af5b09f88114eea2cb766cabf0f4e8))

## [0.11.2](https://github.com/fortytwoservices/mlz-core/compare/v0.11.1...v0.11.2) (2026-11-19)


### Features

* **pipeline:** an apply refuses a changed state or a superseded change, and decommission waits for the applied teardown ([#100](https://github.com/fortytwoservices/mlz-core/issues/100)) ([946a6ab](https://github.com/fortytwoservices/mlz-core/commit/946a6abd7ece2b67971233dc04a1f2e23cea6dbd))
* **pipeline:** plan in groups on the daily check and on large runs ([#98](https://github.com/fortytwoservices/mlz-core/issues/98)) ([762f1a0](https://github.com/fortytwoservices/mlz-core/commit/762f1a03094c89b23a26357080de56fb24829eea))
* **reference:** who owns each module input ([#102](https://github.com/fortytwoservices/mlz-core/issues/102)) ([68882e2](https://github.com/fortytwoservices/mlz-core/commit/68882e2b98a922c63421ece1f57eb8c06e933030))
* **workload:** the landing zone's network, tags and what vending owns, in the workload repo ([#101](https://github.com/fortytwoservices/mlz-core/issues/101)) ([cfc8455](https://github.com/fortytwoservices/mlz-core/commit/cfc84553ba29895597c2bc91d6d5fa666f05c935))

## [0.11.1](https://github.com/fortytwoservices/mlz-core/compare/v0.11.0...v0.11.1) (2026-11-18)


### Features

* **pipeline:** plan only the stacks a change touches ([#96](https://github.com/fortytwoservices/mlz-core/issues/96)) ([907f1b1](https://github.com/fortytwoservices/mlz-core/commit/907f1b1343fe71eb946cbffc46b91402fcee9bd7))

## [0.11.0](https://github.com/fortytwoservices/mlz-core/compare/v0.10.2...v0.11.0) (2026-11-18)


### ⚠ BREAKING CHANGES

* name each settings folder after its stack ([#94](https://github.com/fortytwoservices/mlz-core/issues/94))
  In the upgrade pull request itself, move each repo's folders (there is no migration code). Platform: `hubs/` to `connectivity/`, `firewall/` to `firewall-rules/`, and for a hub file with `identity: true`, delete the line and add an empty `identity/<region>.yaml`. Before that pull request's plan, a state operator copies two blobs in the platform's state container, `platform.tfstate` to `connectivity.tfstate` and `firewall.tfstate` to `firewall-rules.tfstate`, and deletes the old ones once `main` has run on this release. Vending: `landing-zones/_defaults/all.yaml` to `defaults/landing-zone-defaults.yaml`, `landing-zones/_defaults/<env>.yaml` to `defaults/<env>-defaults.yaml`, each `_workload.yaml` to `workload-defaults.yaml`, `landing-zones/_examples/` to `examples/`. Governance: nothing. Every stack's plan on the pull request must then show no changes.

### Features

* name each settings folder after its stack ([#94](https://github.com/fortytwoservices/mlz-core/issues/94)) ([c326c5c](https://github.com/fortytwoservices/mlz-core/commit/c326c5c48640181464e1ded5e1fbfc3805d8b846))

## [0.10.2](https://github.com/fortytwoservices/mlz-core/compare/v0.10.1...v0.10.2) (2026-11-18)


### Bug Fixes

* clearer messages from mlz install, mlz upgrade and the apply ([#92](https://github.com/fortytwoservices/mlz-core/issues/92)) ([e9b10dc](https://github.com/fortytwoservices/mlz-core/commit/e9b10dc48ff654fa65b4bfdd8ab02e68a381c788))
* **platform:** wait while no hub file has a setting, so a first deployment does not deadlock ([#91](https://github.com/fortytwoservices/mlz-core/issues/91)) ([a5e90ef](https://github.com/fortytwoservices/mlz-core/commit/a5e90ef8d7007a8425c8e9dfc974735d8b057354))

## [0.10.1](https://github.com/fortytwoservices/mlz-core/compare/v0.10.0...v0.10.1) (2026-11-18)


### Bug Fixes

* **vending:** leave a workload repo's merge settings to the team once it is made ([#89](https://github.com/fortytwoservices/mlz-core/issues/89)) ([9bfb0bd](https://github.com/fortytwoservices/mlz-core/commit/9bfb0bdfe152e41e47f93209a39e425a57c3b660))

## [0.10.0](https://github.com/fortytwoservices/mlz-core/compare/v0.9.0...v0.10.0) (2026-11-18)


### ⚠ BREAKING CHANGES

* **vending:** read a workload repo's environment with the Actions permission ([#86](https://github.com/fortytwoservices/mlz-core/issues/86))
  Before upgrading, add **Actions: Read-only** to the customer's MLZ App (its settings, Permissions & events, Repository permissions), and have an organization owner accept the new permission on the installation. A token that asks for a permission the installation has not granted fails, and with it every vending run.

### Bug Fixes

* **governance:** alz provider 0.22.1, which no longer crashes on an empty policy version list ([#85](https://github.com/fortytwoservices/mlz-core/issues/85)) ([57c457d](https://github.com/fortytwoservices/mlz-core/commit/57c457d3ccb56be13f404d21515ef2f699f69cab))
* **mirror:** refuse mlz-core itself as the organization's copy ([#84](https://github.com/fortytwoservices/mlz-core/issues/84)) ([4360221](https://github.com/fortytwoservices/mlz-core/commit/4360221e0767ea82f3643c117cacb54034984edf))
* **vending:** exempt a corp landing zone's state account from Deny-Public-Endpoints ([#87](https://github.com/fortytwoservices/mlz-core/issues/87)) ([b8a6e1d](https://github.com/fortytwoservices/mlz-core/commit/b8a6e1db07cd861acfcea991edf570a166b29a16))
* **vending:** read a workload repo's environment with the Actions permission ([#86](https://github.com/fortytwoservices/mlz-core/issues/86)) ([59efebf](https://github.com/fortytwoservices/mlz-core/commit/59efebff66814b437ee8747416a161cc69b24b26))

## [0.9.0](https://github.com/fortytwoservices/mlz-core/compare/v0.8.1...v0.9.0) (2026-11-16)


### ⚠ BREAKING CHANGES

* `bootstrap.yaml`'s `github.runner_size` is replaced by `github.runners` (`size` under it), and `mlz install runners` sets up self-hosted runners unless `github.runners.kind` is `github-hosted`. New repos call the organization's own copy of mlz-core instead of `fortytwoservices/mlz-core`. No customer runs MLZ yet.

### Features

* **runners:** Fortytwo's dev image, for its own tests without an image fee ([#82](https://github.com/fortytwoservices/mlz-core/issues/82)) ([17f3b81](https://github.com/fortytwoservices/mlz-core/commit/17f3b81b6f73ff650efc67d30234ce09b53b29bf))
* self-hosted runners by default, and the organization's own copy of mlz-core ([#81](https://github.com/fortytwoservices/mlz-core/issues/81)) ([50d6a14](https://github.com/fortytwoservices/mlz-core/commit/50d6a14fe7bc22f1dc686165375671a6b7589ef1))

## [0.8.1](https://github.com/fortytwoservices/mlz-core/compare/v0.8.0...v0.8.1) (2026-11-15)


### Features

* **owners:** the run on main gives a team CODEOWNERS names write access ([#79](https://github.com/fortytwoservices/mlz-core/issues/79)) ([75d5aa5](https://github.com/fortytwoservices/mlz-core/commit/75d5aa5c507caf04847199092426d2d75d6096e6))

## [0.8.0](https://github.com/fortytwoservices/mlz-core/compare/v0.7.0...v0.8.0) (2026-11-15)


### ⚠ BREAKING CHANGES

* **firewall:** rules say why, never reach the whole internet, and may come before their landing zone ([#75](https://github.com/fortytwoservices/mlz-core/issues/75))

### Features

* **firewall:** rules say why, never reach the whole internet, and may come before their landing zone ([#75](https://github.com/fortytwoservices/mlz-core/issues/75)) ([31a885e](https://github.com/fortytwoservices/mlz-core/commit/31a885e8d56292cb35be63dfbd1c08a83139d98a))
* **install:** a merge into main waits for the pipeline's check ([#78](https://github.com/fortytwoservices/mlz-core/issues/78)) ([a26fbd0](https://github.com/fortytwoservices/mlz-core/commit/a26fbd0ea1afb097a1aefc057c50971ac216fdd1))
* **install:** the platform team as code owner of the three repos ([#76](https://github.com/fortytwoservices/mlz-core/issues/76)) ([d6f13f7](https://github.com/fortytwoservices/mlz-core/commit/d6f13f77ccb51580bbd138ad5a6112b5feed7d43))

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
