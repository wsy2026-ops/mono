function getValue(give){
    if(ITEM_DATA[give].value)return ITEM_DATA[give].value;
    var value = 0;
    if(ITEM_DATA[give].effect){
        for(var attr in ITEM_DATA[give].effect){
            var mul = 1;
            if(attr == 'ps')mul *= 0.5;
            value += mul * (((ITEM_DATA[give].effect[attr] > 0) && (attr != 'temp'))?ITEM_DATA[give].effect[attr]:0);
        }
    }else{
        var done = false;
        function check(data){
            if(data[give]){
                var require = data[give].require;
                for(var attr in require){
                    value += getValue(attr) * require[attr];
                    done = true;
                }
                return true;
            }
            return false;
        }
        if(!check(MAKE_DATA)){
            if(!check(ALCHEMY_DATA)){
                if(!check(MAGIC_DATA)){
                }
            }
        }
    }
    return value;
}

var RequireComponent = React.createClass({
    getDefaultProps:function(){
        return{
            requireList:null,
            withSpace:false,
            separator:null,
        }
    },
    render:function(){
        var requireList = this.props.requireList;
        function mapRes(){
            var result = [];
            var count = 0;
            var total = getLength(requireList);
            for (var attr in requireList) {
                // if(count > 0 && count%2 == 0)result.push(<br key = {'br_'+count}/>);
                if(count > 0&&this.props.withSpace)result.push(<br key = {'br_' + count}/>);
                var amount = requireList[attr];
                var name = ITEM_DATA[attr]?ITEM_DATA[attr].name:STATE_DATA[attr].name;
                result.push(<span key = {count} className = "resourceName" >
                                {name}
                                <span className = "badge resourceAmount">
                                    {amount}
                                </span>
                                {total == count + 1 ? null:this.props.separator}
                            </span>);
                count ++;
            };
            return result;
        }
        return  <span>
                    {mapRes.bind(this)()}
                </span>
    }
});

var MainComponent = React.createClass({
    getMstType:function(){
        var result = []
        function sortBy(a,b){
            lll(a.damage + ' ' + b.damage + ' ' + (a.damage >= b.damage));
            return a.damage - b.damage;
        }
        var arr = [];
        for(var attr in MST_DATA){
            arr.push(clone(MST_DATA[attr]));
        }
        arr.sort(sortBy)
        for(var i = 0; i < arr.length; i++){
            var _tmp = arr[i]; 
            result.push(<tr key = {arr[i].name}>
                            <td>{_tmp.name||null}</td>
                            <td>{_tmp.maxHp||null}</td>
                            <td>{(_tmp.maxHp * (_tmp.hpMul || 1))||null}</td>
                            <td>{_tmp.damage||null}</td>
                            <td>{_tmp.range||null}</td>
                            <td><RequireComponent requireList = {_tmp.reward}></RequireComponent></td>
                            <td><RequireComponent requireList = {_tmp.chanceGet}></RequireComponent></td>
                            <td>{_tmp.chaseChance||null}</td>
                        </tr>)
        }
        lll(result)
        return result;
    },
    getWeaponType:function(type){
        var result = []
        function sortBy(a,b){
            return a.damage - b.damage;
        }
        var arr = []
        for(var attr in ITEM_DATA){
            var tmp = clone(ITEM_DATA[attr]);
            if(tmp.weaponType == type){
                var o = clone(ITEM_DATA[attr]);
                o.value = getValue(attr);
                arr.push(o);
            }
        }
        arr.sort(sortBy)
        for(var i = 0; i < arr.length; i++){
            var _tmp = arr[i]; 
            result.push(<tr key = {arr[i].name}>
                            <td>{_tmp.name||null}</td>
                            <td>{_tmp.weaponType||null}</td>
                            <td>{_tmp.desc||null}</td>
                            <td>{_tmp.range||null}</td>
                            <td>{_tmp.damage||null}</td>
                            <td><RequireComponent requireList = {_tmp.require}></RequireComponent></td>
                            <td>{_tmp.durable||null}</td>
                            <td>{_tmp.value||null}</td>
                        </tr>)
        }
        return result;
    },
    getEquipType:function(type){
        var result = [];
        var arr = [];
        function sortBy(a,b){
            return getValue(a.attr) - getValue(b.attr);
        }
        for(var attr in ITEM_DATA){
            var tmp = clone(ITEM_DATA[attr]);
            if(tmp.equipType == type){
                var o = clone(ITEM_DATA[attr]);
                o.attr = attr;
                o.value = getValue(attr);
                arr.push(o);
            }
        }
        arr.sort(sortBy);
        for(var i = 0; i < arr.length; i++){
            var _tmp = arr[i]; 
            result.push(<tr key = {arr[i].name}>
                            <td>{_tmp.name||null}</td>
                            <td>{_tmp.equipType||null}</td>
                            <td>{_tmp.desc||null}</td>
                            <td>{_tmp.value||null}</td>
                        </tr>)
        }
        return result;
    },
    getFoodType:function(type){
        var result = []
        var arr = []
        for(var attr in ITEM_DATA){
            var tmp = clone(ITEM_DATA[attr]);
            if(tmp.type == type){
                var o = clone(ITEM_DATA[attr]);
                o.value = getValue(attr);
                arr.push(o);
            }
        }
        for(var i = 0; i < arr.length; i++){
            var _tmp = arr[i]; 
            result.push(<tr key = {arr[i].name}>
                            <td>{_tmp.name||null}</td>
                            <td><RequireComponent requireList = {_tmp.effect} isGreen = {true}></RequireComponent></td>
                            <td>{_tmp.desc||null}</td>
                            <td>{_tmp.value||null}</td>
                        </tr>)
        }
        return result;
    },
    getEvent:function(){
        var result = [];
        var arr = [];
        for(var attr in EVENT_DATA){
            if(!EVENT_DATA[attr].desc)continue;
            var tmp = clone(EVENT_DATA[attr]);
            arr.push(tmp);
        }
        for(var i = 0; i < arr.length; i++){
            var _tmp = arr[i]; 
            result.push(<tr key = {Math.random()}>
                            <td>{_tmp.name||null}</td>
                            <td><RequireComponent requireList = {_tmp.want} isGreen = {true}></RequireComponent></td>
                            <td><RequireComponent requireList = {_tmp.get} isGreen = {true}></RequireComponent></td>
                            <td>{_tmp.desc||null}</td>
                            <td>{_tmp.d_1||null}</td>
                            <td>{_tmp.d_2||null}</td>
                        </tr>)
        }
        return result;
    },
    getStudio:function(attachData){
        var result = [];
        var arr = [];
        for(var attr in attachData){
            var tmp = clone(attachData[attr]);
            tmp.attr = attr;
            arr.push(tmp);
        }
        for(var i = 0; i < arr.length; i++){
            var _tmp = arr[i]; 
            result.push(<tr key = {Math.random()}>
                            <td>{ITEM_DATA[_tmp.attr].name||null}</td>
                            <td><RequireComponent requireList = {_tmp.require} isGreen = {true}></RequireComponent></td>
                            <td>{_tmp.timeNeed}</td>
                            <td>{_tmp.science?ITEM_DATA[_tmp.science].name:null}</td>
                            <td>{_tmp.event?_tmp.event:null}</td>
                        </tr>)
        }
        return result;
    },
    render:function(){
        return (<div>
                    <div className = 'win'>
                        <p><a href="#mst">怪物图鉴</a></p>
                        <p><a href="#weapon">武器图鉴</a></p>
                        <p><a href="#equip">装备图鉴</a></p>
                        <p><a href="#food">食物图鉴</a></p>
                        <p><a href="#quest">任务图鉴</a></p>
                        <p><a href="#make">制作图鉴</a></p>
                    </div>
                    <h1 id = 'mst'>怪物图鉴</h1>
                    <table className = "table  table-hover itemTable">
                        <thead>
                            <tr>
                                <td>名称</td>
                                <td>绝对生命</td>
                                <td>有效生命</td>
                                <td>伤害</td>
                                <td>射程</td>
                                <td>奖励</td>
                                <td>几率</td>
                                <td>追杀</td>
                            </tr>
                        </thead>
                        <tbody>
                            {this.getMstType()}
                        </tbody>
                    </table>
                    <h1 id = 'weapon'>武器图鉴</h1>
                    <table className = "table table-hover itemTable ">
                        <thead>
                            <tr>
                                <td>名称</td>
                                <td>武器</td>
                                <td>描述</td>
                                <td>射程</td>
                                <td>伤害</td>
                                <td>费用</td>
                                <td>耐久</td>
                                <td>价值</td>
                            </tr>
                        </thead>
                        <tbody>
                            {this.getWeaponType('melee')}
                            {this.getWeaponType('shoot')}
                            {this.getWeaponType('magic')}
                        </tbody>
                    </table>
                    <h1 id = 'equip'>装备图鉴</h1>
                    <table className = "table table-hover itemTable ">
                        <thead>
                            <tr>
                                <td>名称</td>
                                <td>部位</td>
                                <td>描述</td>
                                <td>价值</td>
                            </tr>
                        </thead>
                        <tbody>
                            {this.getEquipType('neck')}
                            {this.getEquipType('head')}
                            {this.getEquipType('body')}
                            {this.getEquipType('hand')}
                            {this.getEquipType('foot')}
                        </tbody>
                    </table>
                    <h1 id = 'food'>食物图鉴</h1>
                    <table className = "table table-hover itemTable ">
                        <thead>
                            <tr>
                                <td>名称</td>
                                <td>效果</td>
                                <td>描述</td>
                                <td>价值</td>
                            </tr>
                        </thead>
                        <tbody>
                            {this.getFoodType('food')}
                            {this.getFoodType('cooked')}
                        </tbody>
                    </table>
                    <h1 id = 'quest'>任务图鉴</h1>
                    <table className = "table table-hover itemTable ">
                        <thead>
                            <tr>
                                <td>名称</td>
                                <td>需要</td>
                                <td>获得</td>
                                <td>描述1</td>
                                <td>描述2</td>
                                <td>描述3</td>
                            </tr>
                        </thead>
                        <tbody>
                            {this.getEvent()}
                        </tbody>
                    </table>
                    <h1 id = 'make'>制作图鉴</h1>
                    <table className = "table table-hover itemTable ">
                        <thead>
                            <tr>
                                <td>名称</td>
                                <td>需要</td>
                                <td>耗时</td>
                                <td>科技需求</td>
                                <td>任务需求</td>
                            </tr>
                        </thead>
                        <tbody>
                            {this.getStudio(MAKE_DATA)}
                            {this.getStudio(ALCHEMY_DATA)}
                            {this.getStudio(SCIENCE_DATA)}
                        </tbody>
                    </table>
                </div>)
    }
})

function render(){
    ReactDOM.render(
        <MainComponent />,
        document.getElementById('show')
    );
}

render();