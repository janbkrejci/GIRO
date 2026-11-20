# T120 — Z Přehledu na projektový Kanban a z karty na běh

Odkaz z názvu i kódu projektu na Přehledu používá existující trasu `#/r/<repo>/backlog/project/<projekt>`. Po načtení otevře Kanban s filtrem daného projektu v daném repozitáři bez ohledu na předchozí režim. Jednorázový parametr se spotřebuje, takže další změny filtru a obnovení nepřepíše. Neznámý nebo archivovaný projekt otevře nefiltrovaný Kanban.

Běžný vstup do Backlogu obnoví režim z `giro.backlog.mode`; bez platné uložené volby zachová výchozí Strom. Filtr projektu zůstává uložen zvlášť pro každé repo. Změna režimu nemění rozbalení Stromu.

Tělo běžící karty odkazuje na nejnovější běžící běh tasku. Tělo selhané karty odkazuje na nejnovější selhaný nebo abortovaný běh mimo blokování závislostmi. Rozhoduje aktuální stav karty; starší selhání nepřebije nový běžící pokus. ID se načte z existujícího detailu tasku. Chybějící ID odpovídajícího běhu, neúspěšné načtení nebo ostatní stavy zachovají odkaz na task. Opožděná odpověď po změně karet či repozitáře se ignoruje.

Odkazy zůstávají nativními odkazy, přepínače nativními ovládacími prvky. Ovládání karet i drag-and-drop zůstává zachováno.
