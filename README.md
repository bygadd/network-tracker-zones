# Network Tracker Zones

Свързва клиентските `device_tracker` ентитита от **UniFi Network** и **MikroTik Router** с правилната зона в Home Assistant. Използва съществуващите интеграции и техните тракери; не изисква допълнителни акаунти или търсене на ID в логове.

## Настройка след инсталиране през HACS

**HACS само изтегля интеграцията. Екранът за конфигурация е в Home Assistant, не в HACS.**

1. След изтеглянето рестартирай **Home Assistant**.
2. Отвори **Settings → Devices & services → Integrations → Add integration** и потърси **Network Tracker Zones**. Можеш и да използваш [директния бутон за добавяне](https://my.home-assistant.io/redirect/config_flow_start/?domain=network_tracker_zones).
3. Избери вече добавен **UniFi Network** хъб или **MikroTik Router** от списъка **Източник** и неговата географска **Зона**. Натисни **Submit**.
4. Повтори **Add integration → Network Tracker Zones** за всеки следващ хъб/рутер. Всеки източник има собствено правило и зона.

Ако интеграцията не излиза в **Add integration**, провери дали в HACS статусът е **Downloaded**, рестартирай Home Assistant и обнови страницата на браузъра. Ако няма източници в списъка, първо добави съответните UniFi Network/MikroTik Router интеграции. Ако няма желаната зона, създай я в **Settings → Areas, labels & zones → Zones**.

## Проверка и корекция на вече добавени тракери

Отвори **Settings → Devices & services → Network Tracker Zones** и натисни **Configure** на правилото за конкретния източник. Избери **Провери несъответствията**. Ще видиш тракерите, чиято текуща асоциирана зона е различна от избраната за този хъб. Избери **всички** или само определени тракери и потвърди на следващия екран. Прегледът сам по себе си не променя нищо.

Новите клиентски тракери получават зоната автоматично. Съществуващите ръчно зададени зони се запазват, докато не избереш изрично корекция. След корекция тракерът се управлява от правилото; ако по-късно смениш зоната на правилото, тя ще се актуализира и на този тракер. Нова ръчна промяна на тракера отново има предимство.

Ако проверката съобщи **няма подходящи клиентски тракери**, избраният източник още не е създал разпознати тракери. Съобщението **няма несъответствия** означава, че намерените клиентски тракери вече са в правилната зона.

## Ограничения

Поддържат се UniFi Network (`unifi`) и MikroTik Router (`mikrotik_router`). UniFi AP Direct и вградената MikroTik интеграция са извън обхвата. Интеграцията не създава нови тракери и не възстановява ентити, изгубено поради конфликт на идентификатор в източника. При премахване на правило вече записаните асоциирани зони остават на тракерите.

Кодът е проверен с Home Assistant Core **2026.9.4**. Ако настройката не се отваря след рестарт, виж **Settings → System → Logs** за `network_tracker_zones` и отвори [issue](https://github.com/bygadd/network-tracker-zones/issues) с текста на грешката.

---

## English

HACS downloads the integration but does not configure it. After downloading, **restart Home Assistant**, then open **Settings → Devices & services → Integrations → Add integration → Network Tracker Zones**, or use the [direct setup link](https://my.home-assistant.io/redirect/config_flow_start/?domain=network_tracker_zones). Select an existing UniFi Network or MikroTik Router source and an active zone. Repeat for each source.

To inspect existing trackers, open **Settings → Devices & services → Network Tracker Zones → Configure** for the source, then **Review mismatched trackers**. Select all or individual trackers and confirm the repair. Future clients are assigned automatically; existing manual choices are preserved unless explicitly repaired. The integration does not create tracker entities or fix upstream identity collisions.
